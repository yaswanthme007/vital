from typing import Dict, List, Optional

from app.models.base import CamelModel


class CalibrationProfile(CamelModel):
    id: str
    theatre_id: Optional[str] = None
    camera_id: Optional[str] = None
    layout_id: str
    homography: List[List[float]]
    roi_boxes: Dict[str, List[float]]
    color_map: Dict[str, str]
    created_at: float
