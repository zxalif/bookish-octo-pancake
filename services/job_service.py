"""
Job Service for Background Opportunity Generation

Manages job status for async opportunity generation tasks.
"""

from typing import Dict, Optional
from datetime import datetime
from enum import Enum
import uuid

from core.logger import get_logger

logger = get_logger(__name__)


class JobStatus(str, Enum):
    """Job status enumeration."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobService:
    """Service for managing background job status."""
    
    # In-memory job storage (for MVP)
    # TODO: Move to Redis for production
    _jobs: Dict[str, Dict] = {}
    
    @staticmethod
    def create_job(
        user_id: str,
        keyword_search_id: str,
        limit: int = 100
    ) -> str:
        """
        Create a new job and return job ID.
        
        Args:
            user_id: User UUID
            keyword_search_id: Keyword search UUID
            limit: Maximum opportunities to generate
            
        Returns:
            str: Job ID
        """
        job_id = str(uuid.uuid4())
        
        JobService._jobs[job_id] = {
            "id": job_id,
            "user_id": user_id,
            "keyword_search_id": keyword_search_id,
            "limit": limit,
            "status": JobStatus.PENDING,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "progress": 0,
            "message": "Job queued",
            "result": None,
            "error": None
        }
        
        logger.info(f"Created job {job_id} for user {user_id}")
        return job_id
    
    @staticmethod
    def get_job(job_id: str) -> Optional[Dict]:
        """
        Get job status by ID.
        
        Args:
            job_id: Job UUID
            
        Returns:
            dict: Job status or None if not found
        """
        return JobService._jobs.get(job_id)
    
    @staticmethod
    def update_job_status(
        job_id: str,
        status: JobStatus,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        result: Optional[Dict] = None,
        error: Optional[str] = None
    ):
        """
        Update job status.
        
        Args:
            job_id: Job UUID
            status: New status
            progress: Progress percentage (0-100)
            message: Status message
            result: Job result (when completed)
            error: Error message (when failed)
        """
        if job_id not in JobService._jobs:
            logger.warning(f"Job {job_id} not found")
            return
        
        job = JobService._jobs[job_id]
        job["status"] = status
        job["updated_at"] = datetime.utcnow()
        
        if progress is not None:
            job["progress"] = progress
        if message is not None:
            job["message"] = message
        if result is not None:
            job["result"] = result
        if error is not None:
            job["error"] = error
        
        logger.info(f"Updated job {job_id} status to {status}")
    
    @staticmethod
    def get_user_jobs(user_id: str) -> list:
        """
        Get all jobs for a user.
        
        Args:
            user_id: User UUID
            
        Returns:
            list: List of job dictionaries
        """
        return [
            job for job in JobService._jobs.values()
            if job["user_id"] == user_id
        ]
    
    @staticmethod
    def cleanup_old_jobs(max_age_hours: int = 24):
        """
        Clean up old completed/failed jobs.
        
        Args:
            max_age_hours: Maximum age in hours
        """
        cutoff = datetime.utcnow().timestamp() - (max_age_hours * 3600)
        
        jobs_to_remove = [
            job_id for job_id, job in JobService._jobs.items()
            if job["status"] in [JobStatus.COMPLETED, JobStatus.FAILED]
            and job["updated_at"].timestamp() < cutoff
        ]
        
        for job_id in jobs_to_remove:
            del JobService._jobs[job_id]
        
        if jobs_to_remove:
            logger.info(f"Cleaned up {len(jobs_to_remove)} old jobs")

