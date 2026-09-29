"""Public Sentinel-1 GRD discovery/download for OilTrace."""
from __future__ import annotations
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote
import httpx
from app.config import settings

logger = logging.getLogger(__name__)
STAC_URL = settings.AWS_SENTINEL1_STAC_URL.rstrip('/')
COLLECTION = settings.AWS_SENTINEL1_COLLECTION
SCENE_RE = re.compile(r'^[A-Za-z0-9_.-]+$')


def _utc(dt):
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def search_sentinel1(bbox=None, start_datetime=None, end_datetime=None, polarization='vv', limit=12):
    if bbox and len(bbox) != 4:
        raise ValueError('bbox must be [min_lon,min_lat,max_lon,max_lat]')
    if bbox and not (-180 <= bbox[0] < bbox[2] <= 180 and -90 <= bbox[1] < bbox[3] <= 90):
        raise ValueError('Invalid WGS84 bbox')
    payload = {'collections': [COLLECTION], 'limit': min(max(int(limit), 1), 50)}
    if bbox:
        payload['bbox'] = bbox
    if start_datetime or end_datetime:
        start = _utc(start_datetime) if start_datetime else '..'
        end = _utc(end_datetime) if end_datetime else '..'
        payload['datetime'] = f'{start}/{end}'
    payload['sortby'] = [{'field': 'properties.datetime', 'direction': 'desc'}]
    try:
        r = httpx.post(f'{STAC_URL}/search', json=payload, headers={'Accept': 'application/geo+json'}, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        logger.exception('AWS Sentinel-1 STAC search failed')
        raise RuntimeError(f'Sentinel-1 AWS STAC search failed: {exc}') from exc

    results = []
    for item in data.get('features', []):
        assets = item.get('assets', {})
        thumbnail = next((link.get('href') for link in item.get('links', []) if link.get('rel') == 'thumbnail'), None)
        if not thumbnail and assets.get('thumbnail'):
            thumbnail = assets['thumbnail'].get('href')
        key = polarization.lower()
        asset = assets.get(key)
        if asset is None:
            for k in ('vv', 'vh', 'hh', 'hv'):
                if k in assets:
                    key, asset = k, assets[k]
                    break
        props = item.get('properties', {})
        results.append({
            'id': item.get('id'),
            'datetime': props.get('datetime'),
            'bbox': item.get('bbox'),
            'geometry': item.get('geometry'),
            'asset_key': key,
            'asset_url': asset.get('href') if asset else None,
            'asset_type': asset.get('type') if asset else None,
            'preview_url': thumbnail,
            'platform': props.get('platform'),
            'instrument_mode': props.get('sar:instrument_mode'),
            'polarizations': props.get('sar:polarizations'),
            'properties': {k: v for k, v in props.items() if k.startswith('sar:') or k.startswith('sat:') or k.startswith('proj:')}
        })
    logger.info('AWS Sentinel-1 STAC search success | scenes=%d', len(results))
    return results


def get_scene(scene_id):
    if not SCENE_RE.match(scene_id):
        raise ValueError('Invalid Sentinel-1 scene id')
    try:
        r = httpx.get(f'{STAC_URL}/collections/{COLLECTION}/items/{scene_id}', headers={'Accept': 'application/json'}, timeout=40)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.exception('AWS Sentinel-1 scene lookup failed | scene=%s', scene_id)
        raise RuntimeError(f'Sentinel-1 scene lookup failed: {exc}') from exc


def resolve_asset(scene_id, polarization='vv'):
    item = get_scene(scene_id)
    assets = item.get('assets', {})
    key = polarization.lower()
    asset = assets.get(key)
    if asset is None:
        for k in ('vv', 'vh', 'hh', 'hv'):
            if k in assets:
                key, asset = k, assets[k]
                break
    if not asset or not asset.get('href'):
        raise RuntimeError(f'No {polarization.upper()} GeoTIFF asset found for scene {scene_id}')
    return asset['href'], key, item


def _http_download_url(href: str) -> str:
    """Convert a public AWS S3 href to an HTTPS URL usable by httpx.

    AWS/STAC may legitimately return ``s3://bucket/key``. That URI is valid
    for AWS CLI/boto3 but httpx only accepts HTTP(S), which otherwise causes
    "Request URL has an unsupported protocol 's3://'".
    """
    if not href:
        raise RuntimeError('Sentinel-1 asset has no download URL')
    if href.startswith(('https://', 'http://')):
        return href
    if href.startswith('s3://'):
        raw = href[5:]
        bucket, sep, key = raw.partition('/')
        if not sep or not bucket or not key:
            raise RuntimeError(f'Invalid S3 Sentinel-1 asset URL: {href}')
        region = getattr(settings, 'AWS_SENTINEL1_REGION', 'eu-central-1')
        return f'https://{bucket}.s3.{region}.amazonaws.com/{quote(key, safe="/")}'
    protocol = href.split(':', 1)[0] if ':' in href else 'unknown'
    raise RuntimeError(f'Unsupported Sentinel-1 asset URL protocol: {protocol}')


def resolve_download_url(scene_id, polarization='vv'):
    """Resolve a scene asset to an HTTP URL for raster window reads."""
    href, key, item = resolve_asset(scene_id, polarization)
    return _http_download_url(href), key, item


def download_scene(scene_id, polarization='vv'):
    url, key, item = resolve_asset(scene_id, polarization)
    http_url = _http_download_url(url)
    dest = settings.DATA_DIR / 'sentinel1'
    dest.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', scene_id)
    out = dest / f'{safe}_{key}.tiff'
    if out.exists() and out.stat().st_size > 0:
        logger.info('AWS Sentinel-1 cache hit | %s', out)
        return out, item

    tmp = out.with_suffix('.part')
    logger.info('AWS Sentinel-1 download start | scene=%s | asset=%s | source=%s', scene_id, key.upper(), http_url)
    try:
        with httpx.stream('GET', http_url, timeout=300, follow_redirects=True, headers={'Accept': 'image/tiff,*/*'}) as r:
            r.raise_for_status()
            content_type = (r.headers.get('content-type') or '').lower()
            with tmp.open('wb') as f:
                for chunk in r.iter_bytes(1024 * 1024):
                    if chunk:
                        f.write(chunk)
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise RuntimeError('Sentinel-1 asset download returned an empty file')
        # Reject common HTML/XML error bodies that can otherwise look like a
        # successful HTTP download. Real Sentinel-1 GRD assets are TIFF/binary.
        with tmp.open('rb') as f:
            signature = f.read(16)
        if signature.startswith(b'<') and ('xml' in content_type or 'html' in content_type or signature.lstrip().startswith(b'<')):
            raise RuntimeError('Sentinel-1 asset endpoint returned XML/HTML instead of a GeoTIFF')
        tmp.replace(out)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        logger.exception('AWS Sentinel-1 download failed | scene=%s | url=%s', scene_id, http_url)
        raise RuntimeError(f'Sentinel-1 download failed: {exc}') from exc

    logger.info('AWS Sentinel-1 download success | %.1f MB', out.stat().st_size / 1048576)
    return out, item
