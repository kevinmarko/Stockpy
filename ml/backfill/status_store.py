from typing import Dict, Any, Optional

# In-memory store for job statuses. 
# Key: job_id
# Value: dict with keys: id, name, status, progress, message, start_time, end_time
_status_store: Dict[str, Dict[str, Any]] = {}

def get_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    return _status_store.get(job_id)

def set_job_status(job_id: str, status_data: Dict[str, Any]) -> None:
    if job_id not in _status_store:
        _status_store[job_id] = {}
    _status_store[job_id].update(status_data)

def create_job(job_id: str, name: str) -> None:
    import datetime
    _status_store[job_id] = {
        "id": job_id,
        "name": name,
        "status": "PENDING",
        "progress": 0,
        "message": "Initializing...",
        "start_time": datetime.datetime.now().isoformat(),
        "end_time": None
    }
