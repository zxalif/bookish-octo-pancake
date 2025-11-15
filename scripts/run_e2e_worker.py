#!/usr/bin/env python3
"""
E2E Worker Runner with Auto-Reload Support

This script runs the E2E worker with optional file watching for development.
In production, it runs the worker directly without watching.

Usage:
    python scripts/run_e2e_worker.py              # Production mode (no watch)
    python scripts/run_e2e_worker.py --watch      # Development mode (with watch)
"""

import os
import sys
import subprocess
import signal
import time
from pathlib import Path
from typing import List, Optional

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import get_settings
from core.logger import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)
settings = get_settings()

# Global process reference
worker_process: Optional[subprocess.Popen] = None
shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested, worker_process
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    shutdown_requested = True
    if worker_process:
        try:
            worker_process.terminate()
            worker_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker_process.kill()
        except Exception as e:
            logger.error(f"Error stopping worker process: {e}")
    sys.exit(0)


def get_watch_paths() -> tuple[List[Path], List[Path]]:
    """Get paths to watch for changes."""
    base_path = Path(__file__).parent.parent
    
    # Directories to watch recursively
    watch_dirs = [
        base_path / "core",
        base_path / "services",
        base_path / "models",
        base_path / "scripts",
    ]
    
    # Specific files to watch
    watch_files = [
        base_path / "scripts" / "e2e_worker.py",
        base_path / "services" / "e2e_test_service.py",
    ]
    
    # Filter to only existing paths
    watch_dirs = [d for d in watch_dirs if d.exists()]
    watch_files = [f for f in watch_files if f.exists()]
    
    return watch_dirs, watch_files


def should_reload(file_path: Path) -> bool:
    """Determine if a file change should trigger a reload."""
    # Ignore cache files
    if "__pycache__" in str(file_path) or file_path.suffix == ".pyc":
        return False
    
    # Only reload on Python file changes
    if file_path.suffix == ".py":
        return True
    
    return False


def run_worker() -> subprocess.Popen:
    """Start the E2E worker process."""
    worker_script = Path(__file__).parent / "e2e_worker.py"
    if not worker_script.exists():
        raise FileNotFoundError(f"Worker script not found: {worker_script}")
    
    logger.info("Starting E2E worker process...")
    process = subprocess.Popen(
        [sys.executable, str(worker_script)],
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=os.environ.copy()
    )
    return process


def run_with_watch():
    """Run worker with file watching for auto-reload."""
    try:
        from watchfiles import watch
    except ImportError:
        logger.error("watchfiles not installed. Install with: pip install watchfiles")
        logger.info("Falling back to non-watch mode...")
        run_without_watch()
        return
    
    logger.info("=" * 60)
    logger.info("E2E Worker Service - Development Mode (Auto-Reload Enabled)")
    logger.info("=" * 60)
    logger.info("Watching for file changes...")
    logger.info("Press Ctrl+C to stop\n")
    
    watch_dirs, watch_files = get_watch_paths()
    
    if not watch_dirs and not watch_files:
        logger.warning("No paths to watch found. Running without watch mode.")
        run_without_watch()
        return
    
    logger.info(f"Watching {len(watch_dirs)} directories and {len(watch_files)} files:")
    for d in watch_dirs:
        logger.info(f"  - {d}")
    for f in watch_files:
        logger.info(f"  - {f}")
    logger.info("")
    
    global worker_process, shutdown_requested
    
    # Start initial worker
    worker_process = run_worker()
    last_reload = time.time()
    
    try:
        # Watch for file changes
        for changes in watch(*watch_dirs, *watch_files, recursive=True):
            if shutdown_requested:
                break
            
            # Debounce: wait 1 second before reloading
            if time.time() - last_reload < 1.0:
                continue
            
            reload_needed = False
            changed_files = []
            
            for change_type, file_path in changes:
                file_path = Path(file_path)
                
                if should_reload(file_path):
                    reload_needed = True
                    changed_files.append(file_path)
            
            if reload_needed:
                logger.info(f"\n🔄 File changed: {', '.join(str(f.name) for f in changed_files[:3])}")
                if len(changed_files) > 3:
                    logger.info(f"   ... and {len(changed_files) - 3} more files")
                
                # Stop current process
                if worker_process:
                    logger.info("⏹️  Stopping current worker process...")
                    try:
                        worker_process.terminate()
                        worker_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        logger.warning("Worker process didn't stop in time, killing...")
                        worker_process.kill()
                        worker_process.wait()
                    except Exception as e:
                        logger.error(f"Error stopping worker: {e}")
                
                # Wait a moment
                time.sleep(0.5)
                
                # Restart
                logger.info("🔄 Reloading worker...\n")
                worker_process = run_worker()
                last_reload = time.time()
                
    except KeyboardInterrupt:
        logger.info("\nReceived keyboard interrupt, shutting down...")
        shutdown_requested = True
    except Exception as e:
        logger.error(f"Error in watch loop: {e}", exc_info=True)
    finally:
        if worker_process:
            try:
                worker_process.terminate()
                worker_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker_process.kill()
            except Exception:
                pass


def run_without_watch():
    """Run worker without file watching (production mode)."""
    logger.info("=" * 60)
    logger.info("E2E Worker Service - Production Mode")
    logger.info("=" * 60)
    
    global worker_process, shutdown_requested
    
    worker_process = run_worker()
    
    try:
        # Wait for process to complete
        worker_process.wait()
    except KeyboardInterrupt:
        logger.info("\nReceived keyboard interrupt, shutting down...")
        shutdown_requested = True
    finally:
        if worker_process:
            try:
                worker_process.terminate()
                worker_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker_process.kill()
            except Exception:
                pass


def main():
    """Main entry point."""
    global shutdown_requested
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Check if watch mode is enabled
    watch_mode = os.environ.get("WATCH_MODE", "false").lower() == "true"
    watch_mode = watch_mode or "--watch" in sys.argv or "-w" in sys.argv
    
    # In production, never use watch mode
    if settings.ENVIRONMENT == "production":
        watch_mode = False
        logger.info("Production environment detected. Watch mode disabled.")
    
    try:
        if watch_mode:
            run_with_watch()
        else:
            run_without_watch()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

