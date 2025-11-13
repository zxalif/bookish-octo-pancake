"""
Job Service for Background Opportunity Generation

Manages job status for async opportunity generation tasks.
Uses Redis for persistence, falls back to in-memory storage if Redis is unavailable.
"""

from typing import Dict, Optional
from datetime import datetime
from enum import Enum
import uuid
import json

from core.logger import get_logger
from core.redis_client import get_redis_client, is_redis_available

logger = get_logger(__name__)

# Job key prefix in Redis
JOB_KEY_PREFIX = "job:"
JOB_TTL_SECONDS = 86400 * 7  # 7 days


class JobStatus(str, Enum):
    """Job status enumeration."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobService:
    """Service for managing background job status."""
    
    # Fallback in-memory job storage (if Redis unavailable)
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
        
        job_data = {
            "id": job_id,
            "user_id": user_id,
            "keyword_search_id": keyword_search_id,
            "limit": limit,
            "status": JobStatus.PENDING,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "progress": 0,
            "message": "Job queued",
            "result": None,
            "error": None
        }
        
        # Try Redis first
        redis_client = get_redis_client()
        if redis_client and is_redis_available():
            try:
                job_key = f"{JOB_KEY_PREFIX}{job_id}"
                redis_client.setex(
                    job_key,
                    JOB_TTL_SECONDS,
                    json.dumps(job_data)
                )
                logger.info(f"Created job {job_id} in Redis for user {user_id}")
                return job_id
            except Exception as e:
                logger.warning(f"Failed to store job in Redis, falling back to memory: {str(e)}")
        
        # Fallback to in-memory storage
        job_data["created_at"] = datetime.utcnow()
        job_data["updated_at"] = datetime.utcnow()
        JobService._jobs[job_id] = job_data
        logger.info(f"Created job {job_id} in memory for user {user_id}")
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
        # Try Redis first
        redis_client = get_redis_client()
        if redis_client and is_redis_available():
            try:
                job_key = f"{JOB_KEY_PREFIX}{job_id}"
                job_data = redis_client.get(job_key)
                if job_data:
                    job_dict = json.loads(job_data)
                    # Convert ISO strings back to datetime objects for compatibility
                    if isinstance(job_dict.get("created_at"), str):
                        job_dict["created_at"] = datetime.fromisoformat(job_dict["created_at"])
                    if isinstance(job_dict.get("updated_at"), str):
                        job_dict["updated_at"] = datetime.fromisoformat(job_dict["updated_at"])
                    return job_dict
            except Exception as e:
                logger.warning(f"Failed to get job from Redis, falling back to memory: {str(e)}")
        
        # Fallback to in-memory storage
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
        # Try Redis first
        redis_client = get_redis_client()
        if redis_client and is_redis_available():
            try:
                job_key = f"{JOB_KEY_PREFIX}{job_id}"
                job_data = redis_client.get(job_key)
                
                if not job_data:
                    logger.warning(f"Job {job_id} not found in Redis")
                    return
                
                job = json.loads(job_data)
                job["status"] = status
                job["updated_at"] = datetime.utcnow().isoformat()
                
                if progress is not None:
                    job["progress"] = progress
                if message is not None:
                    job["message"] = message
                if result is not None:
                    job["result"] = result
                if error is not None:
                    job["error"] = error
                
                # Update in Redis
                redis_client.setex(
                    job_key,
                    JOB_TTL_SECONDS,
                    json.dumps(job)
                )
                logger.info(f"Updated job {job_id} status to {status} in Redis")
                return
            except Exception as e:
                logger.warning(f"Failed to update job in Redis, falling back to memory: {str(e)}")
        
        # Fallback to in-memory storage
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
        
        logger.info(f"Updated job {job_id} status to {status} in memory")
    
    @staticmethod
    def get_user_jobs(user_id: str) -> list:
        """
        Get all jobs for a user.
        
        Args:
            user_id: User UUID
            
        Returns:
            list: List of job dictionaries
        """
        # Try Redis first
        redis_client = get_redis_client()
        if redis_client and is_redis_available():
            try:
                # Get all job keys
                job_keys = redis_client.keys(f"{JOB_KEY_PREFIX}*")
                user_jobs = []
                
                for job_key in job_keys:
                    job_data = redis_client.get(job_key)
                    if job_data:
                        job = json.loads(job_data)
                        if job.get("user_id") == user_id:
                            # Convert ISO strings back to datetime objects
                            if isinstance(job.get("created_at"), str):
                                job["created_at"] = datetime.fromisoformat(job["created_at"])
                            if isinstance(job.get("updated_at"), str):
                                job["updated_at"] = datetime.fromisoformat(job["updated_at"])
                            user_jobs.append(job)
                
                return user_jobs
            except Exception as e:
                logger.warning(f"Failed to get user jobs from Redis, falling back to memory: {str(e)}")
        
        # Fallback to in-memory storage
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
        
        # Try Redis first
        redis_client = get_redis_client()
        if redis_client and is_redis_available():
            try:
                job_keys = redis_client.keys(f"{JOB_KEY_PREFIX}*")
                jobs_removed = 0
                
                for job_key in job_keys:
                    job_data = redis_client.get(job_key)
                    if job_data:
                        job = json.loads(job_data)
                        updated_at_str = job.get("updated_at")
                        if updated_at_str:
                            updated_at = datetime.fromisoformat(updated_at_str)
                            if (job["status"] in [JobStatus.COMPLETED, JobStatus.FAILED] and
                                updated_at.timestamp() < cutoff):
                                redis_client.delete(job_key)
                                jobs_removed += 1
                
                if jobs_removed > 0:
                    logger.info(f"Cleaned up {jobs_removed} old jobs from Redis")
                return
            except Exception as e:
                logger.warning(f"Failed to cleanup jobs in Redis, falling back to memory: {str(e)}")
        
        # Fallback to in-memory storage
        jobs_to_remove = [
            job_id for job_id, job in JobService._jobs.items()
            if job["status"] in [JobStatus.COMPLETED, JobStatus.FAILED]
            and job["updated_at"].timestamp() < cutoff
        ]
        
        for job_id in jobs_to_remove:
            del JobService._jobs[job_id]
        
        if jobs_to_remove:
            logger.info(f"Cleaned up {len(jobs_to_remove)} old jobs from memory")

