#!/usr/bin/env python3
"""
E2E Test Worker Service

Isolated service that polls Redis for E2E test jobs and executes them.
Completely separate from the API to prevent resource contention.

This service:
1. Polls Redis for E2E test jobs
2. Executes Playwright tests in isolation
3. Stores results in database
4. Handles Playwright browser installation automatically
"""

import os
import sys
import time
import signal
import json
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import SessionLocal
from core.logger import get_logger, setup_logging
from core.config import get_settings
from core.redis_client import get_redis_client, is_redis_available
from services.e2e_test_service import E2ETestService
import traceback

# Initialize logging
setup_logging()
logger = get_logger(__name__)
settings = get_settings()

# Global flag for graceful shutdown
shutdown_requested = False

# Redis queue keys
E2E_JOB_QUEUE = "e2e_test_jobs"
E2E_RESULT_PREFIX = "e2e_test_result:"

# Check Playwright installation
async def _check_browser_installed():
    """Check if Playwright browsers are installed by trying to launch one."""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            await browser.close()
        return True
    except Exception as e:
        logger.debug(f"Browser check failed: {str(e)}")
        return False


def ensure_playwright_installed():
    """Ensure Playwright browsers are installed."""
    import asyncio
    import subprocess
    import os
    
    # First, try to verify installation by checking if browser executable exists
    # Support both /root/.cache (legacy) and /app/.cache (current) paths
    playwright_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/app/.cache/ms-playwright")
    
    # Check if browser directory exists (quick check)
    import glob
    chromium_pattern = os.path.join(playwright_path, "chromium_headless_shell-*", "chrome-linux", "headless_shell")
    if glob.glob(chromium_pattern):
        # Browser files exist, try to verify it works
        try:
            # Run async check in sync context
            # Create new event loop to avoid RuntimeWarning
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If loop is already running, we can't use asyncio.run()
                    # Just skip verification and assume it's installed
                    logger.info("Playwright browsers appear to be installed (skipping verification in running loop)")
                    return True
            except RuntimeError:
                pass  # No event loop, safe to use asyncio.run()
            
            is_installed = asyncio.run(_check_browser_installed())
            if is_installed:
                logger.info("Playwright browsers are installed and working")
                return True
        except Exception as e:
            logger.debug(f"Browser verification failed: {str(e)}, will reinstall")
    
    # Browser not installed or not working, install it
    logger.warning("Playwright browsers not installed or not working")
    logger.info("Installing Playwright browsers...")
    try:
        # Install chromium browser
        result = subprocess.run(
            ["python", "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout (browser download can be slow)
        )
        if result.returncode == 0:
            logger.info("Playwright browsers installed successfully")
            # Also install system dependencies
            logger.info("Installing Playwright system dependencies...")
            deps_result = subprocess.run(
                ["python", "-m", "playwright", "install-deps", "chromium"],
                capture_output=True,
                text=True,
                timeout=300
            )
            if deps_result.returncode == 0:
                logger.info("Playwright system dependencies installed successfully")
            else:
                logger.warning(f"Failed to install system dependencies: {deps_result.stderr}")
            return True
        else:
            logger.error(f"Failed to install Playwright browsers: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("Playwright installation timed out")
        return False
    except Exception as e:
        logger.error(f"Error installing Playwright browsers: {str(e)}")
        return False


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    shutdown_requested = True


async def process_e2e_job(job_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process a single E2E test job.
    
    Args:
        job_data: Job data from Redis queue
        
    Returns:
        dict: Test result
    """
    job_id = job_data.get("job_id")
    triggered_by = job_data.get("triggered_by", "scheduled")
    frontend_url = job_data.get("frontend_url")
    api_url = job_data.get("api_url")
    
    logger.info(f"Processing E2E test job: {job_id} (triggered_by: {triggered_by})")
    
    # Create database session
    db = SessionLocal()
    try:
        # Run the test
        test_result = await E2ETestService.run_full_flow_test(
            db=db,
            triggered_by=triggered_by,
            frontend_url=frontend_url,
            api_url=api_url
        )
        
        # Save test result to database
        test_result_model = E2ETestService.save_test_result(db, test_result)
        
        logger.info(
            f"E2E test job {job_id} completed: "
            f"status={test_result_model.status}, "
            f"duration={test_result_model.duration_ms}ms"
        )
        
        return {
            "job_id": job_id,
            "status": "completed",
            "test_result": test_result_model.to_dict()
        }
        
    except Exception as e:
        logger.error(f"Error processing E2E test job {job_id}: {str(e)}", exc_info=True)
        return {
            "job_id": job_id,
            "status": "failed",
            "error": str(e)
        }
    finally:
        db.close()


async def worker_loop():
    """Main worker loop that polls Redis for jobs."""
    global shutdown_requested
    
    # Ensure Playwright is installed
    if not ensure_playwright_installed():
        logger.error("Failed to install Playwright browsers. Exiting.")
        return
    
    # Poll interval (seconds) - define before Redis client creation
    poll_interval = 5
    
    # Check Redis availability
    if not is_redis_available():
        logger.error("Redis is not available. Exiting.")
        return
    
    # Get Redis client with longer socket timeout for blocking operations
    # BLPOP can block for up to poll_interval seconds, so socket timeout must be longer
    from core.config import get_settings
    import redis
    settings = get_settings()
    
    try:
        redis_client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=10,
            socket_timeout=poll_interval + 10,  # Longer than BLPOP timeout to avoid socket timeout
            retry_on_timeout=True,
            health_check_interval=30
        )
        # Test connection
        redis_client.ping()
        logger.info("Redis connection established for worker")
    except Exception as e:
        logger.error(f"Failed to get Redis client: {str(e)}. Exiting.")
        return
    
    logger.info("=" * 60)
    logger.info("E2E Test Worker Service Starting")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Redis Queue: {E2E_JOB_QUEUE}")
    logger.info("=" * 60)
    
    while not shutdown_requested:
        try:
            # Blocking pop from Redis list (BLPOP with timeout)
            # This is more efficient than polling - blocks until a job is available
            # blpop returns None if timeout, or tuple (queue_name, value) if job found
            result = redis_client.blpop(E2E_JOB_QUEUE, timeout=poll_interval)
            
            if result:
                # result is a tuple: (queue_name, job_data_json)
                # Redis client is configured with decode_responses=True, so values are strings
                queue_name, job_data_json = result
                
                try:
                    # Parse job data
                    job_data = json.loads(job_data_json)
                    job_id = job_data.get("job_id")
                    
                    logger.info(f"Received E2E test job: {job_id}")
                    
                    # Process the job
                    result_data = await process_e2e_job(job_data)
                    
                    # Store result in Redis (optional - for quick lookup)
                    if job_id:
                        redis_client.setex(
                            f"{E2E_RESULT_PREFIX}{job_id}",
                            3600,  # 1 hour TTL
                            json.dumps(result_data)
                        )
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse job data: {str(e)}")
                except Exception as e:
                    logger.error(f"Error processing job: {str(e)}", exc_info=True)
            
            # No job available (timeout is expected and normal)
            # Continue loop to check again
            
        except redis.exceptions.TimeoutError:
            # Timeout is expected when no jobs are available - this is normal
            # BLPOP timeout means no job was available, continue polling
            if not shutdown_requested:
                continue
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
            shutdown_requested = True
        except redis.exceptions.ConnectionError as e:
            logger.error(f"Redis connection error: {str(e)}. Retrying in {poll_interval}s...")
            await asyncio.sleep(poll_interval)
            # Try to reconnect
            try:
                redis_client.ping()
                logger.info("Redis connection restored")
            except:
                pass
        except Exception as e:
            logger.error(f"Error in worker loop: {str(e)}", exc_info=True)
            await asyncio.sleep(poll_interval)  # Wait before retrying
    
    logger.info("E2E Test Worker Service stopped")


def main():
    """Main entry point."""
    global shutdown_requested
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Run the async worker loop
        asyncio.run(worker_loop())
    except Exception as e:
        logger.error(f"Fatal error in E2E worker: {str(e)}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

