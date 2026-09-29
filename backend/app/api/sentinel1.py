from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.services.sentinel1_aws import search_sentinel1,get_scene,download_scene
from app.security import require_api_key
logger=logging.getLogger(__name__)
router=APIRouter(prefix='/api/v1/sentinel1',tags=['sentinel-1'])
@router.get('/search')
def search(min_lon:Optional[float]=Query(None),min_lat:Optional[float]=Query(None),max_lon:Optional[float]=Query(None),max_lat:Optional[float]=Query(None),start:Optional[datetime]=Query(None),end:Optional[datetime]=Query(None),polarization:str=Query('vv'),limit:int=Query(12,ge=1,le=50)):
    try:
        vals=[min_lon,min_lat,max_lon,max_lat]; bbox=None
        if any(v is not None for v in vals):
            if not all(v is not None for v in vals): raise ValueError('All four bbox values are required together')
            bbox=vals
        return {'source':'AWS Open Data / Earth Search','collection':'sentinel-1-grd','scenes':search_sentinel1(bbox,start,end,polarization,limit)}
    except Exception:
        logger.exception('Sentinel-1 search error'); raise HTTPException(status_code=502,detail={"type": "sentinel1-unavailable", "title": "Sentinel-1 unavailable", "detail": "Sentinel-1 search failed."}) from None
@router.get('/scene/{scene_id}')
def scene(scene_id:str):
    try: return get_scene(scene_id)
    except Exception:
        logger.exception('Sentinel-1 scene error'); raise HTTPException(status_code=404,detail={"type": "not-found", "title": "Not found", "detail": "Sentinel-1 scene not found."}) from None
class DownloadRequest(BaseModel):
    scene_id:str
    polarization:str=Field('vv',pattern='^(vv|vh|hh|hv)$')
@router.post('/download', dependencies=[Depends(require_api_key)])
def download(req:DownloadRequest):
    try:
        path,item=download_scene(req.scene_id,req.polarization)
        return {'status':'ready','scene_id':req.scene_id,'polarization':req.polarization,'local_path':str(path),'filename':path.name,'bbox':item.get('bbox'),'datetime':item.get('properties',{}).get('datetime')}
    except Exception:
        logger.exception('Sentinel-1 download error'); raise HTTPException(status_code=502,detail={"type": "sentinel1-unavailable", "title": "Sentinel-1 unavailable", "detail": "Sentinel-1 download failed."}) from None
