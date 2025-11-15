#!/usr/bin/env python3
"""
Scheduler Service for ClientHunt

Runs scheduled jobs for:
- Subscription management (sync, expired, past_due, renewals)
- Usage metrics refresh
- Cleanup operations

This service runs as a separate container and executes jobs on a schedule.
"""

import os
import sys
import time
import signal
import threading
from datetime import datetime
from typing import Optional, Dict
from concurrent.futures import ThreadPoolExecutor, Future
import queue

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import SessionLocal, engine
from core.logger import get_logger, setup_logging
from core.config import get_settings
from services.subscription_management_service import SubscriptionManagementService
from services.cleanup_service import CleanupService
from services.lead_refresh_service import LeadRefreshService
import asyncio

# Initialize logging
setup_logging()
logger = get_logger(__name__)
settings = get_settings()

# Global flag for graceful shutdown
shutdown_requested = False

# Thread pool for concurrent job execution
# Max 5 concurrent jobs to avoid overwhelming the database
executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="scheduler-job")

# Track running jobs to prevent duplicate execution
running_jobs: Dict[str, Future] = {}
running_jobs_lock = threading.Lock()


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    shutdown_requested = True


def run_job(job_name: str, job_func, *args, **kwargs):
    """
    Run a scheduled job and handle errors.
    
    This function is called from within a thread, so each job runs concurrently.
    
    Args:
        job_name: Name of the job for logging
        job_func: Function to execute
        *args: Positional arguments for job function
        **kwargs: Keyword arguments for job function
    """
    try:
        logger.info(f"Starting job: {job_name}")
        start_time = time.time()
        
        result = job_func(*args, **kwargs)
        
        elapsed = time.time() - start_time
        logger.info(f"Completed job: {job_name} in {elapsed:.2f}s - Result: {result}")
        
        return result
    except Exception as e:
        logger.error(f"Error running job {job_name}: {str(e)}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        # Remove from running jobs when done
        with running_jobs_lock:
            if job_name in running_jobs:
                del running_jobs[job_name]


def sync_subscriptions_job():
    """Sync subscriptions with Paddle (run hourly)."""
    # Each job gets its own database session (thread-safe)
    db = SessionLocal()
    try:
        return run_job(
            "sync_subscriptions",
            SubscriptionManagementService.sync_subscriptions_with_paddle,
            db
        )
    finally:
        db.close()


def process_expired_subscriptions_job():
    """Process expired subscriptions (run daily at 2 AM)."""
    db = SessionLocal()
    try:
        return run_job(
            "process_expired_subscriptions",
            SubscriptionManagementService.process_expired_subscriptions,
            db
        )
    finally:
        db.close()


def process_past_due_subscriptions_job():
    """Process past_due subscriptions (run daily at 3 AM)."""
    db = SessionLocal()
    try:
        return run_job(
            "process_past_due_subscriptions",
            SubscriptionManagementService.process_past_due_subscriptions,
            db
        )
    finally:
        db.close()


def check_upcoming_renewals_job():
    """Check upcoming renewals (run daily at 9 AM)."""
    db = SessionLocal()
    try:
        return run_job(
            "check_upcoming_renewals",
            SubscriptionManagementService.check_upcoming_renewals,
            db,
            days_ahead=3
        )
    finally:
        db.close()


def refresh_usage_metrics_job():
    """Refresh usage metrics (run daily at midnight)."""
    db = SessionLocal()
    try:
        return run_job(
            "refresh_usage_metrics",
            SubscriptionManagementService.refresh_usage_metrics,
            db
        )
    finally:
        db.close()


def cleanup_old_searches_job():
    """Cleanup old soft-deleted searches (run daily at 2 AM)."""
    db = SessionLocal()
    try:
        return run_job(
            "cleanup_old_searches",
            CleanupService.cleanup_old_soft_deleted_searches,
            db,
            days_old=30
        )
    finally:
        db.close()


def monthly_cleanup_job():
    """Monthly cleanup reset (run on 1st of month at 00:01)."""
    db = SessionLocal()
    try:
        return run_job(
            "monthly_cleanup",
            CleanupService.cleanup_current_month_soft_deleted_searches,
            db
        )
    finally:
        db.close()


def cleanup_old_page_visits_job():
    """Cleanup old page visits (run daily at 2:10 AM)."""
    db = SessionLocal()
    try:
        return run_job(
            "cleanup_old_page_visits",
            CleanupService.cleanup_old_page_visits,
            db,
            months_old=3
        )
    finally:
        db.close()


def refresh_leads_job():
    """Refresh leads from Rixly and send email notifications (run every 6 hours)."""
    db = SessionLocal()
    try:
        # Run async function in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                LeadRefreshService.refresh_leads_for_all_users(db)
            )
            return result
        finally:
            loop.close()
    finally:
        db.close()


def should_run_job(job_name: str, current_hour: int, current_minute: int, current_day: int) -> bool:
    """
    Determine if a job should run based on current time.
    
    Args:
        job_name: Name of the job
        current_hour: Current hour (0-23)
        current_minute: Current minute (0-59)
        current_day: Current day of month (1-31)
        
    Returns:
        bool: True if job should run
    """
    job_schedules = {
        "sync_subscriptions": lambda h, m, d: m == 0,  # Run every hour at minute 0
        "refresh_usage_metrics": lambda h, m, d: h == 0 and m == 0,  # Daily at midnight
        "process_expired_subscriptions": lambda h, m, d: h == 2 and m == 0,  # Daily at 2 AM
        "cleanup_old_searches": lambda h, m, d: h == 2 and m == 5,  # Daily at 2:05 AM
        "cleanup_old_page_visits": lambda h, m, d: h == 2 and m == 10,  # Daily at 2:10 AM
        "process_past_due_subscriptions": lambda h, m, d: h == 3 and m == 0,  # Daily at 3 AM
        "check_upcoming_renewals": lambda h, m, d: h == 9 and m == 0,  # Daily at 9 AM
        "refresh_leads": lambda h, m, d: h in [0, 6, 12, 18] and m == 0,  # Every 6 hours at minute 0
        "monthly_cleanup": lambda h, m, d: d == 1 and h == 0 and m == 1,  # 1st of month at 00:01
    }
    
    schedule_func = job_schedules.get(job_name)
    if schedule_func:
        return schedule_func(current_hour, current_minute, current_day)
    
    return False


def submit_job(job_name: str, job_func, *args, **kwargs) -> bool:
    """
    Submit a job to run concurrently.
    
    Prevents duplicate execution if the same job is already running.
    
    Args:
        job_name: Name of the job
        job_func: Function to execute
        *args: Positional arguments for job function
        **kwargs: Keyword arguments for job function
        
    Returns:
        bool: True if job was submitted, False if already running
    """
    with running_jobs_lock:
        # Check if job is already running
        if job_name in running_jobs:
            future = running_jobs[job_name]
            if not future.done():
                logger.debug(f"Job {job_name} is already running, skipping...")
                return False
        
        # Submit job to thread pool
        future = executor.submit(job_func, *args, **kwargs)
        running_jobs[job_name] = future
        logger.info(f"Submitted job {job_name} to thread pool")
        return True


def wait_for_migrations(max_wait_seconds: int = 300):
    """
    Wait for database migrations to complete before starting scheduler.
    
    This ensures the database schema is up-to-date before jobs run.
    
    Args:
        max_wait_seconds: Maximum time to wait for migrations (default: 5 minutes)
    """
    import subprocess
    import os
    import time
    from sqlalchemy import text
    
    # Check if we should wait for migrations
    auto_run_migrations = os.getenv("AUTO_RUN_MIGRATIONS", "false").lower() == "true"
    
    if auto_run_migrations:
        logger.info("AUTO_RUN_MIGRATIONS is enabled, running migrations...")
        try:
            result = subprocess.run(
                ["alembic", "upgrade", "head"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd="/app"
            )
            if result.returncode == 0:
                logger.info("Database migrations completed successfully")
            else:
                logger.warning(f"Migration warning: {result.stderr}")
                logger.warning("Continuing anyway, but database may not be up-to-date")
        except Exception as e:
            logger.warning(f"Could not run migrations automatically: {str(e)}")
            logger.warning("Continuing anyway, but database may not be up-to-date")
    else:
        # Wait for API service to complete migrations (check database connection)
        logger.info("Waiting for database to be ready and migrations to complete...")
        logger.info("(API service should run migrations on startup)")
        start_time = time.time()
        db_ready = False
        
        while not db_ready and (time.time() - start_time) < max_wait_seconds:
            try:
                # Try to connect to database
                db = SessionLocal()
                try:
                    # Simple query to check if database is accessible
                    db.execute(text("SELECT 1"))
                    db.commit()
                    db_ready = True
                    logger.info("Database connection established")
                except Exception as e:
                    logger.debug(f"Database not ready yet: {str(e)}")
                    time.sleep(5)
                finally:
                    db.close()
            except Exception as e:
                logger.debug(f"Database connection failed: {str(e)}")
                time.sleep(5)
        
        if not db_ready:
            logger.warning(f"Database not ready after {max_wait_seconds}s, continuing anyway...")
        else:
            logger.info("Database is ready, scheduler can start running jobs")


def main():
    """Main scheduler loop."""
    global shutdown_requested
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info("=" * 60)
    logger.info("ClientHunt Scheduler Service Starting")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Database: {settings.DATABASE_URL.split('@')[-1] if '@' in settings.DATABASE_URL else 'N/A'}")
    logger.info("=" * 60)
    
    # Wait for migrations to complete before starting jobs
    wait_for_migrations()
    
    # Track last run times to avoid duplicate runs
    last_run_times = {}
    
    # Main loop - check every minute
    while not shutdown_requested:
        try:
            now = datetime.utcnow()
            current_hour = now.hour
            current_minute = now.minute
            current_day = now.day
            
            # Sync subscriptions (run every hour at minute 0)
            if should_run_job("sync_subscriptions", current_hour, current_minute, current_day):
                if "sync_subscriptions" not in last_run_times or \
                   (now - last_run_times["sync_subscriptions"]).total_seconds() >= 3600:
                    if submit_job("sync_subscriptions", sync_subscriptions_job):
                        last_run_times["sync_subscriptions"] = now
            
            # Refresh usage metrics (daily at midnight)
            if should_run_job("refresh_usage_metrics", current_hour, current_minute, current_day):
                if "refresh_usage_metrics" not in last_run_times or \
                   (now - last_run_times["refresh_usage_metrics"]).days >= 1:
                    if submit_job("refresh_usage_metrics", refresh_usage_metrics_job):
                        last_run_times["refresh_usage_metrics"] = now
            
            # Process expired subscriptions (daily at 2 AM)
            if should_run_job("process_expired_subscriptions", current_hour, current_minute, current_day):
                if "process_expired_subscriptions" not in last_run_times or \
                   (now - last_run_times["process_expired_subscriptions"]).days >= 1:
                    if submit_job("process_expired_subscriptions", process_expired_subscriptions_job):
                        last_run_times["process_expired_subscriptions"] = now
            
            # Cleanup old searches (daily at 2:05 AM)
            if should_run_job("cleanup_old_searches", current_hour, current_minute, current_day):
                if "cleanup_old_searches" not in last_run_times or \
                   (now - last_run_times["cleanup_old_searches"]).days >= 1:
                    if submit_job("cleanup_old_searches", cleanup_old_searches_job):
                        last_run_times["cleanup_old_searches"] = now
            
            # Cleanup old page visits (daily at 2:10 AM)
            if should_run_job("cleanup_old_page_visits", current_hour, current_minute, current_day):
                if "cleanup_old_page_visits" not in last_run_times or \
                   (now - last_run_times["cleanup_old_page_visits"]).days >= 1:
                    if submit_job("cleanup_old_page_visits", cleanup_old_page_visits_job):
                        last_run_times["cleanup_old_page_visits"] = now
            
            # Refresh leads from Rixly (every 6 hours)
            if should_run_job("refresh_leads", current_hour, current_minute, current_day):
                if "refresh_leads" not in last_run_times or \
                   (now - last_run_times["refresh_leads"]).total_seconds() >= 21600:  # 6 hours
                    if submit_job("refresh_leads", refresh_leads_job):
                        last_run_times["refresh_leads"] = now
            
            # Process past_due subscriptions (daily at 3 AM)
            if should_run_job("process_past_due_subscriptions", current_hour, current_minute, current_day):
                if "process_past_due_subscriptions" not in last_run_times or \
                   (now - last_run_times["process_past_due_subscriptions"]).days >= 1:
                    if submit_job("process_past_due_subscriptions", process_past_due_subscriptions_job):
                        last_run_times["process_past_due_subscriptions"] = now
            
            # Check upcoming renewals (daily at 9 AM)
            if should_run_job("check_upcoming_renewals", current_hour, current_minute, current_day):
                if "check_upcoming_renewals" not in last_run_times or \
                   (now - last_run_times["check_upcoming_renewals"]).days >= 1:
                    if submit_job("check_upcoming_renewals", check_upcoming_renewals_job):
                        last_run_times["check_upcoming_renewals"] = now
            
            # Monthly cleanup (1st of month at 00:01)
            if should_run_job("monthly_cleanup", current_hour, current_minute, current_day):
                if "monthly_cleanup" not in last_run_times or \
                   (now - last_run_times["monthly_cleanup"]).days >= 28:  # Allow some leeway
                    if submit_job("monthly_cleanup", monthly_cleanup_job):
                        last_run_times["monthly_cleanup"] = now
            
            # Clean up completed futures to prevent memory leak
            with running_jobs_lock:
                completed_jobs = [
                    job_name for job_name, future in running_jobs.items()
                    if future.done()
                ]
                for job_name in completed_jobs:
                    del running_jobs[job_name]
            
            # Sleep for 60 seconds before next check
            time.sleep(60)
            
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
            shutdown_requested = True
        except Exception as e:
            logger.error(f"Unexpected error in scheduler loop: {str(e)}", exc_info=True)
            # Sleep before retrying to avoid tight error loop
            time.sleep(60)
    
    # Graceful shutdown: wait for running jobs to complete
    logger.info("Shutting down scheduler, waiting for running jobs to complete...")
    
    # Wait for all running jobs to complete (with timeout)
    with running_jobs_lock:
        running_futures = list(running_jobs.values())
    
    if running_futures:
        logger.info(f"Waiting for {len(running_futures)} running job(s) to complete...")
        # Wait up to 5 minutes for jobs to complete
        for future in running_futures:
            try:
                future.result(timeout=300)  # 5 minute timeout
            except Exception as e:
                logger.warning(f"Job did not complete gracefully: {str(e)}")
    
    # Shutdown thread pool
    logger.info("Shutting down thread pool...")
    executor.shutdown(wait=True, timeout=60)
    
    logger.info("Scheduler service stopped")


if __name__ == "__main__":
    main()

