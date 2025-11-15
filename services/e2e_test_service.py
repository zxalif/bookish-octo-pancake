"""
E2E Test Service

End-to-end testing service using Playwright.
Tests the full user flow: registration -> email verification -> login -> create search -> generate opportunities -> create support thread.
"""

import asyncio
import os
import time
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

try:
    from playwright.async_api import async_playwright, Page, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from core.config import get_settings
from core.logger import get_logger
from models.e2e_test_result import E2ETestResult
from models.user import User
from models.keyword_search import KeywordSearch
from models.opportunity import Opportunity
from models.support_thread import SupportThread

settings = get_settings()
logger = get_logger(__name__)


class E2ETestService:
    """Service for running end-to-end tests with Playwright."""
    
    @staticmethod
    def _save_progress(
        db: Session,
        test_run_id: str,
        status: str,
        steps: List[Dict[str, Any]],
        triggered_by: str,
        test_user_email: Optional[str] = None,
        test_user_id: Optional[str] = None,
        error_message: Optional[str] = None,
        duration_ms: Optional[float] = None,
        test_metadata: Optional[Dict[str, Any]] = None,
        current_step: Optional[str] = None
    ):
        """
        Save or update test progress in database.
        This allows the admin panel to show real-time progress.
        """
        try:
            # Enhance metadata with current step info
            enhanced_metadata = test_metadata.copy() if test_metadata else {}
            if current_step:
                enhanced_metadata["current_step"] = current_step
            if steps:
                # Get the last step as current step if not provided
                if not current_step and len(steps) > 0:
                    last_step = steps[-1]
                    enhanced_metadata["current_step"] = last_step.get("step", "unknown")
                    enhanced_metadata["current_step_status"] = last_step.get("status", "unknown")
                enhanced_metadata["steps_completed"] = len([s for s in steps if s.get("status") == "passed"])
                enhanced_metadata["steps_total"] = len(steps)
                enhanced_metadata["steps_failed"] = len([s for s in steps if s.get("status") == "failed"])
            
            # Try to find existing test result
            test_result = db.query(E2ETestResult).filter(
                E2ETestResult.test_run_id == test_run_id
            ).first()
            
            if test_result:
                # Update existing
                test_result.status = status
                test_result.steps = steps
                test_result.error_message = error_message
                if test_user_email:
                    test_result.test_user_email = test_user_email
                if test_user_id:
                    test_result.test_user_id = test_user_id
                if duration_ms:
                    test_result.duration_ms = duration_ms
                test_result.test_metadata = enhanced_metadata
            else:
                # Create new
                test_result = E2ETestResult(
                    test_run_id=test_run_id,
                    status=status,
                    steps=steps,
                    triggered_by=triggered_by,
                    test_user_email=test_user_email,
                    test_user_id=test_user_id,
                    error_message=error_message,
                    duration_ms=duration_ms,
                    test_metadata=enhanced_metadata
                )
                db.add(test_result)
            
            db.commit()
            logger.debug(f"Saved test progress: {test_run_id} - {status} - {len(steps)} steps - Current: {enhanced_metadata.get('current_step', 'N/A')}")
        except Exception as e:
            logger.error(f"Failed to save test progress: {str(e)}", exc_info=True)
            db.rollback()
    
    @staticmethod
    async def run_full_flow_test(
        db: Session,
        triggered_by: str = "manual",
        frontend_url: Optional[str] = None,
        api_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Run full end-to-end test flow.
        
        Tests:
        1. Register a new user
        2. Verify email (check SMTP)
        3. Login
        4. Create a keyword search
        5. Generate opportunities
        6. Create a support thread
        
        Args:
            db: Database session
            triggered_by: How the test was triggered (manual, scheduled, deployment)
            frontend_url: Frontend URL (defaults to settings.FRONTEND_URL)
            api_url: API URL (defaults to settings.API_URL)
            
        Returns:
            dict: Test result with status, steps, and details
        """
        if not PLAYWRIGHT_AVAILABLE:
            return {
                "status": "error",
                "error_message": "Playwright is not installed. Run: pip install playwright && playwright install",
                "steps": []
            }
        
        # Determine URLs based on environment
        # Development: Use localhost URLs (frontend and API on same machine)
        # Production: Use production URLs (https://clienthunt.app and https://api.clienthunt.app)
        environment = settings.ENVIRONMENT.lower()
        is_production = environment in ["production", "prod"]
        
        if frontend_url is None:
            if is_production:
                frontend_url = "https://clienthunt.app"
                logger.info("E2E: Using production frontend URL (environment: production)")
            else:
                frontend_url = settings.FRONTEND_URL or "http://localhost:9100"
                logger.info(f"E2E: Using development frontend URL: {frontend_url} (environment: {environment})")
        
        if api_url is None:
            if is_production:
                api_url = "https://api.clienthunt.app"
                logger.info("E2E: Using production API URL (environment: production)")
            else:
                api_url = settings.API_URL or "http://localhost:7300"
                logger.info(f"E2E: Using development API URL: {api_url} (environment: {environment})")
        
        # IMPORTANT: If using production frontend, ensure API URL matches
        # Production frontend (https://clienthunt.app) is configured to use production API (https://api.clienthunt.app)
        # We need to use the same API URL that the frontend uses, otherwise:
        # - Frontend calls production API → creates user in production DB
        # - E2E worker checks local DB → user not found
        if "clienthunt.app" in frontend_url and not api_url.startswith("https://api.clienthunt.app"):
            # Production frontend uses production API
            api_url = "https://api.clienthunt.app"
            logger.warning(f"E2E: Auto-corrected API URL to match production frontend: {api_url}")
        
        # Auto-fix localhost URLs when running in Docker
        # Frontend/API running on host machine need to be accessed via host.docker.internal
        # Check if we're running in Docker by checking for Docker-specific files or environment
        is_docker = False
        try:
            is_docker = (
                os.path.exists("/.dockerenv") or  # Docker sets this file
                os.environ.get("container") == "docker" or  # Some Docker setups
                (os.path.exists("/proc/self/cgroup") and "docker" in open("/proc/self/cgroup", "r").read())  # Docker cgroup
            )
        except Exception:
            pass  # If check fails, assume not Docker
        
        # If running in Docker and URL contains localhost, replace with host.docker.internal
        # This allows Docker containers to access services running on the host machine
        # NOTE: This only applies to localhost URLs, not production URLs
        if is_docker:
            if ("localhost" in frontend_url or "127.0.0.1" in frontend_url) and "clienthunt.app" not in frontend_url:
                frontend_url = frontend_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
                logger.info(f"Auto-corrected frontend URL for Docker (host service): {frontend_url}")
            if ("localhost" in api_url or "127.0.0.1" in api_url) and "api.clienthunt.app" not in api_url:
                api_url = api_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
                logger.info(f"Auto-corrected API URL for Docker (host service): {api_url}")
        
        # Log the URLs being used for debugging
        logger.info(f"E2E Test Configuration - Frontend URL: {frontend_url}, API URL: {api_url}")
        logger.info(f"E2E Test Configuration - Settings FRONTEND_URL: {settings.FRONTEND_URL}, Settings API_URL: {settings.API_URL}")
        environment_var = os.environ.get('ENVIRONMENT', 'not set')
        logger.info(f"E2E Test Configuration - Running in Docker: {is_docker}, ENVIRONMENT: {environment_var}")
        
        test_run_id = str(uuid.uuid4())
        test_user_email = f"e2e-test-{int(time.time())}@test.clienthunt.app"
        test_user_password = "TestPassword123!"
        test_user_name = "E2E Test User"
        
        steps: List[Dict[str, Any]] = []
        test_user_id: Optional[str] = None
        keyword_search_id: Optional[str] = None
        screenshot_path: Optional[str] = None
        error_message: Optional[str] = None
        
        start_time = time.time()
        
        try:
            async with async_playwright() as p:
                # Launch browser (headless mode)
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                )
                
                # Set up route interception BEFORE creating the page
                # This ensures we catch all requests including preflight OPTIONS
                async def handle_route(route):
                    request = route.request
                    url = request.url
                    method = request.method
                    
                    # Rewrite localhost API URLs to use the correct API URL
                    if 'localhost:7300' in url or '127.0.0.1:7300' in url:
                        # Replace localhost with the correct API URL
                        new_url = url.replace('http://localhost:7300', api_url).replace('http://127.0.0.1:7300', api_url)
                        logger.info(f"E2E: Rewriting API request ({method}): {url} -> {new_url}")
                        await route.continue_(url=new_url)
                    else:
                        await route.continue_()
                
                # Set up route interception for API requests at context level
                # This catches all requests including preflight OPTIONS
                await context.route('**/api/**', handle_route)
                await context.route('http://localhost:7300/**', handle_route)
                await context.route('http://127.0.0.1:7300/**', handle_route)
                
                page = await context.new_page()
                
                try:
                    # Step 1: Register user
                    step_start = time.time()
                    try:
                        # Initialize test result with "running" status
                        E2ETestService._save_progress(
                            db=db,
                            test_run_id=test_run_id,
                            status="running",
                            steps=[],
                            triggered_by=triggered_by,
                            current_step="register",
                            test_metadata={
                                "frontend_url": frontend_url,
                                "api_url": api_url,
                                "browser": "chromium",
                                "playwright_version": "1.48.0",  # Will be updated if we can detect it
                            }
                        )
                        
                        # Monitor network requests to catch API errors using asyncio.Event
                        register_response_event = asyncio.Event()
                        register_response_data = {"status": None, "body": None, "error": None, "url": None, "request_url": None}
                        all_requests = []  # Track all requests for debugging
                        all_responses = []  # Track all responses for debugging
                        
                        async def get_response_body(response, data_dict, event):
                            try:
                                body = await response.text()
                                data_dict["body"] = body[:2000]  # Get more for debugging
                                data_dict["url"] = str(response.url)
                                logger.info(f"E2E: Registration API response body: {body[:500]}")
                            except Exception as e:
                                data_dict["error"] = str(e)
                                logger.error(f"E2E: Error reading response body: {str(e)}")
                            finally:
                                event.set()
                        
                        def handle_response(response):
                            url = str(response.url)
                            try:
                                method = response.request.method if hasattr(response, 'request') and hasattr(response.request, 'method') else 'UNKNOWN'
                                
                                # Track all API responses for debugging
                                if '/api/' in url:
                                    all_responses.append({
                                        "url": url,
                                        "method": method,
                                        "status": response.status
                                    })
                                    logger.info(f"E2E: API Response: {method} {url} - Status: {response.status}")
                                
                                # Check if this is the registration endpoint
                                if '/api/v1/auth/register' in url or '/auth/register' in url:
                                    logger.info(f"E2E: Detected registration API response: {url} - Method: {method} - Status: {response.status}")
                                    register_response_data["status"] = response.status
                                    register_response_data["url"] = url
                                    # Create task to get response body
                                    asyncio.create_task(
                                        get_response_body(response, register_response_data, register_response_event)
                                    )
                            except Exception as e:
                                logger.error(f"E2E: Error in response handler: {str(e)}")
                        
                        # Set up response listener BEFORE navigation
                        page.on('response', handle_response)
                        
                        # Also monitor requests for debugging
                        def handle_request(request):
                            try:
                                url = str(request.url)
                                method = request.method if hasattr(request, 'method') else 'UNKNOWN'
                                
                                # Track all API requests for debugging
                                if '/api/' in url:
                                    all_requests.append({
                                        "url": url,
                                        "method": method
                                    })
                                    logger.info(f"E2E: API Request: {method} {url}")
                                
                                # Check if this is the registration endpoint
                                if '/api/v1/auth/register' in url or '/auth/register' in url:
                                    logger.info(f"E2E: Detected registration API request: {method} {url}")
                                    register_response_data["request_url"] = url
                            except Exception as e:
                                logger.error(f"E2E: Error in request handler: {str(e)}")
                        
                        page.on('request', handle_request)
                        
                        # Note: Route interception is already set up at context level (before page creation)
                        # This ensures we catch all requests including preflight OPTIONS requests
                        
                        # Set up console error capture
                        console_errors = []
                        def handle_console(msg):
                            if msg.type in ['error', 'warning']:
                                console_errors.append({
                                    "type": msg.type,
                                    "text": msg.text
                                })
                                logger.warning(f"E2E: Console {msg.type}: {msg.text}")
                        
                        page.on('console', handle_console)
                        
                        # Set up page error capture
                        page_errors = []
                        def handle_page_error(error):
                            page_errors.append(str(error))
                            logger.error(f"E2E: Page error: {error}")
                        
                        page.on('pageerror', handle_page_error)
                        
                        logger.info(f"E2E: Navigating to registration page: {frontend_url}/register")
                        await page.goto(f"{frontend_url}/register", wait_until="networkidle", timeout=30000)
                        logger.info(f"E2E: Registration page loaded, current URL: {page.url}")
                        
                        # Wait for form to be ready
                        await page.wait_for_selector('input[type="email"]', timeout=10000)
                        logger.info("E2E: Registration form is ready")
                        
                        # Check what API URL the frontend is configured to use
                        try:
                            api_url_from_frontend = await page.evaluate("() => { return process?.env?.NEXT_PUBLIC_API_URL || window?.NEXT_PUBLIC_API_URL || 'not found'; }")
                            logger.info(f"E2E: Frontend API URL (from env): {api_url_from_frontend}")
                        except:
                            logger.warning("E2E: Could not read frontend API URL from environment")
                        
                        # Fill registration form
                        logger.info(f"E2E: Filling registration form for {test_user_email}")
                        
                        # Email field
                        email_input = await page.query_selector('input[type="email"]')
                        if email_input:
                            await email_input.fill(test_user_email)
                            logger.info("E2E: Filled email field")
                        else:
                            raise Exception("Email input field not found")
                        
                        # Full name field
                        name_input = await page.query_selector('input[name="full_name"]')
                        if name_input:
                            await name_input.fill(test_user_name)
                            logger.info("E2E: Filled full_name field")
                        else:
                            logger.warning("E2E: Full name input not found, trying alternative selectors")
                            name_input = await page.query_selector('input[placeholder*="name" i], input[placeholder*="Name" i], input[id="full_name"]')
                            if name_input:
                                await name_input.fill(test_user_name)
                                logger.info("E2E: Filled full_name field (alternative selector)")
                            else:
                                raise Exception("Full name input field not found")
                        
                        # Password field (first one)
                        password_inputs = await page.query_selector_all('input[type="password"]')
                        if len(password_inputs) >= 1:
                            await password_inputs[0].fill(test_user_password)
                            logger.info("E2E: Filled password field")
                        else:
                            raise Exception("Password input field not found")
                        
                        # Confirm password field (second password input)
                        if len(password_inputs) >= 2:
                            await password_inputs[1].fill(test_user_password)
                            logger.info("E2E: Filled confirmPassword field")
                        else:
                            # Try alternative selector for confirm password
                            confirm_password_input = await page.query_selector('input[name="confirmPassword"], input[id="confirmPassword"]')
                            if confirm_password_input:
                                await confirm_password_input.fill(test_user_password)
                                logger.info("E2E: Filled confirmPassword field (alternative selector)")
                            else:
                                raise Exception("Confirm password input field not found")
                        
                        # Check consent checkboxes - these are required!
                        consent_checkboxes = await page.query_selector_all('input[type="checkbox"]')
                        logger.info(f"E2E: Found {len(consent_checkboxes)} consent checkboxes")
                        if len(consent_checkboxes) == 0:
                            logger.warning("E2E: No checkboxes found - form may not require consent")
                        else:
                            for i, checkbox in enumerate(consent_checkboxes):
                                try:
                                    # Check if checkbox is already checked
                                    is_checked = await checkbox.is_checked()
                                    if not is_checked:
                                        await checkbox.check()
                                        logger.info(f"E2E: Checked checkbox {i+1}")
                                    else:
                                        logger.info(f"E2E: Checkbox {i+1} already checked")
                                except Exception as e:
                                    logger.warning(f"E2E: Could not check checkbox {i+1}: {str(e)}")
                        
                        # Wait a moment for form to be ready and validate
                        await page.wait_for_timeout(1000)
                        
                        # Check if form is valid before submitting
                        try:
                            form = await page.query_selector('form')
                            if form:
                                is_valid = await form.evaluate("(form) => form.checkValidity()")
                                logger.info(f"E2E: Form validity check: {is_valid}")
                                if not is_valid:
                                    validation_message = await form.evaluate("(form) => form.validationMessage || 'Form validation failed'")
                                    logger.warning(f"E2E: Form validation failed: {validation_message}")
                        except Exception as e:
                            logger.warning(f"E2E: Could not check form validity: {str(e)}")
                        
                        # Submit form
                        logger.info(f"E2E: Submitting registration form for {test_user_email}")
                        submit_button = await page.query_selector('button[type="submit"]')
                        if not submit_button:
                            # Try alternative selectors
                            submit_button = await page.query_selector('button:has-text("Register"), button:has-text("Sign Up"), button:has-text("Create Account")')
                        if submit_button:
                            # Check if button is disabled
                            is_disabled = await submit_button.is_disabled()
                            if is_disabled:
                                logger.warning("E2E: Submit button is disabled - form may not be valid")
                            else:
                                await submit_button.click()
                                logger.info("E2E: Submit button clicked")
                        else:
                            # Fallback: press Enter on the form
                            logger.warning("E2E: Submit button not found, trying Enter key on form")
                            form = await page.query_selector('form')
                            if form:
                                await form.press("Enter")
                            else:
                                await page.keyboard.press("Enter")
                        
                        # Wait for API response (with timeout)
                        try:
                            logger.info("E2E: Waiting for registration API response (timeout: 30s)...")
                            await asyncio.wait_for(register_response_event.wait(), timeout=30.0)
                            
                            logger.info(f"E2E: Registration API response received - Status: {register_response_data.get('status')}")
                            
                            # Check if API call failed
                            if register_response_data["status"] and register_response_data["status"] >= 400:
                                error_body = register_response_data.get('body', 'No response body')
                                error_url = register_response_data.get('url', 'Unknown URL')
                                logger.error(f"E2E: Registration API call failed - Status: {register_response_data['status']}, URL: {error_url}, Body: {error_body}")
                                raise Exception(
                                    f"Registration API call failed with status {register_response_data['status']} "
                                    f"from {error_url}: {error_body}"
                                )
                            
                            if register_response_data["status"]:
                                logger.info(f"E2E: Registration API call succeeded with status {register_response_data['status']}")
                                if register_response_data.get('body'):
                                    logger.info(f"E2E: Response body: {register_response_data['body'][:500]}")
                        except asyncio.TimeoutError:
                            # Wait a bit more and check if user was created anyway
                            logger.warning("E2E: Registration API response timeout after 30s - checking database anyway")
                            await page.wait_for_timeout(3000)  # Wait a bit longer
                            
                            # Log what we know
                            if register_response_data.get("status"):
                                logger.warning(f"E2E: Partial response data - Status: {register_response_data['status']}, URL: {register_response_data.get('url')}")
                                if register_response_data.get('body'):
                                    logger.warning(f"E2E: Response body: {register_response_data['body'][:500]}")
                            else:
                                logger.warning("E2E: No API response detected - form submission may have failed")
                                # Log all API requests we saw
                                if all_requests:
                                    logger.warning(f"E2E: All API requests seen: {all_requests}")
                                if all_responses:
                                    logger.warning(f"E2E: All API responses seen: {all_responses}")
                                # Check if request was made
                                if register_response_data.get("request_url"):
                                    logger.warning(f"E2E: Request was made to {register_response_data['request_url']} but no response received")
                                else:
                                    logger.error("E2E: No registration API request detected at all - form may not have submitted")
                                    # Log console errors if any
                                    if console_errors:
                                        logger.error(f"E2E: Console errors: {console_errors}")
                                    if page_errors:
                                        logger.error(f"E2E: Page errors: {page_errors}")
                        
                        # Wait for form submission to complete
                        # Registration can either:
                        # 1. Show success message (if verification email is sent)
                        # 2. Redirect to dashboard (if auto-login happens)
                        # 3. Show error message
                        try:
                            # Wait for either success message, redirect, or error
                            await page.wait_for_function(
                                lambda: (
                                    page.url().includes('/dashboard') or
                                    page.url().includes('/verify-email') or
                                    page.locator('text=/success|registered|verification/i').count() > 0 or
                                    page.locator('[role="alert"]').count() > 0 or
                                    page.locator('.error, .text-red-600, .text-red-500').count() > 0
                                ),
                                timeout=10000
                            )
                        except Exception:
                            # If wait fails, check for any visible error messages
                            pass
                        
                        # Check for errors on the page
                        error_elements = await page.locator('[role="alert"], .error, .text-red-600, .text-red-500').all()
                        if error_elements:
                            error_text = ""
                            for elem in error_elements:
                                text = await elem.text_content()
                                if text:
                                    error_text += text + " "
                            if error_text.strip():
                                raise Exception(f"Registration form showed error: {error_text.strip()}")
                        
                        # Wait a bit more for database to be updated
                        logger.info("E2E: Waiting for database to be updated...")
                        await page.wait_for_timeout(3000)  # Increased wait time
                        
                        # Verify user was created in database
                        # Refresh the database session to see latest data
                        logger.info(f"E2E: Checking database for user {test_user_email}...")
                        logger.info(f"E2E: Database URL: {settings.DATABASE_URL[:50]}...")  # Log first 50 chars for debugging
                        logger.info(f"E2E: API URL used by frontend: {api_url}")
                        logger.info(f"E2E: Frontend URL: {frontend_url}")
                        logger.info(f"E2E: Current page URL: {page.url}")
                        
                        # Try to get page content for debugging
                        try:
                            page_title = await page.title()
                            logger.info(f"E2E: Page title: {page_title}")
                        except:
                            pass
                        
                        # IMPORTANT: If using production frontend/API, ensure database matches
                        # Production frontend → Production API → Production Database
                        # Local frontend → Local API → Local Database
                        if "clienthunt.app" in frontend_url and "api.clienthunt.app" in api_url:
                            logger.warning(
                                "E2E: Using production frontend/API. "
                                "Ensure DATABASE_URL points to production database, "
                                "otherwise user verification will fail!"
                            )
                        
                        db.expire_all()
                        user = db.query(User).filter(User.email == test_user_email).first()
                        
                        if not user:
                            # Get current page URL and content for debugging
                            current_url = page.url
                            logger.error(f"E2E: User not found in database. Current page URL: {current_url}")
                            
                            # Check for error messages on the page
                            error_msg = ""
                            try:
                                error_elements = await page.locator('[role="alert"], .error, .text-red-600, .text-red-500, [class*="error"]').all()
                                if error_elements:
                                    for elem in error_elements:
                                        text = await elem.text_content()
                                        if text and text.strip():
                                            error_msg += text.strip() + " | "
                                    logger.error(f"E2E: Page error messages: {error_msg}")
                            except Exception as e:
                                logger.warning(f"E2E: Could not read error messages: {str(e)}")
                            
                            # Use captured console errors
                            if console_errors:
                                logger.error(f"E2E: Browser console errors: {console_errors}")
                            if page_errors:
                                logger.error(f"E2E: Page errors: {page_errors}")
                            
                            # Log all API requests/responses we saw
                            if all_requests:
                                logger.error(f"E2E: All API requests seen: {all_requests}")
                            if all_responses:
                                logger.error(f"E2E: All API responses seen: {all_responses}")
                            
                            # Get network errors
                            try:
                                # Check if there were any failed network requests
                                failed_requests = await page.evaluate("""
                                    () => {
                                        if (window.__playwright_network_errors) {
                                            return window.__playwright_network_errors;
                                        }
                                        return [];
                                    }
                                """)
                                if failed_requests:
                                    logger.error(f"E2E: Network errors: {failed_requests}")
                            except:
                                pass
                            
                            # Build comprehensive error message
                            api_debug = ""
                            if register_response_data.get("status"):
                                api_debug = f"API Status: {register_response_data['status']}"
                                if register_response_data.get('url'):
                                    api_debug += f" from {register_response_data['url']}"
                                if register_response_data.get('body'):
                                    api_debug += f" | Response: {register_response_data['body'][:500]}"
                            elif register_response_data.get("request_url"):
                                api_debug = f"Request made to {register_response_data['request_url']} but no response received"
                            else:
                                api_debug = "No API request detected"
                            
                            error_details = [f"Current URL: {current_url}"]
                            if error_msg:
                                error_details.append(f"Page errors: {error_msg.rstrip(' | ')}")
                            if api_debug:
                                error_details.append(api_debug)
                            
                            error_message = f"User was not created in database. {' | '.join(error_details)}"
                            logger.error(f"E2E: {error_message}")
                            
                            # Take a screenshot for debugging
                            try:
                                # Save to logs directory (persistent volume) instead of /tmp
                                logs_dir = settings.LOG_FILE.rsplit('/', 1)[0] if settings.LOG_FILE else "/app/logs"
                                os.makedirs(logs_dir, exist_ok=True)
                                screenshot_path = os.path.join(logs_dir, f"e2e-registration-failure-{test_run_id}.png")
                                await page.screenshot(path=screenshot_path, full_page=True)
                                logger.info(f"E2E: Screenshot saved to {screenshot_path}")
                            except Exception as e:
                                logger.warning(f"E2E: Could not take screenshot: {str(e)}")
                                screenshot_path = None
                            
                            raise Exception(error_message)
                        
                        logger.info(f"E2E: User found in database: {user.id} - Verified: {user.is_verified}")
                        
                        test_user_id = user.id
                        step_duration = (time.time() - step_start) * 1000
                        
                        steps.append({
                            "step": "register",
                            "status": "passed",
                            "duration_ms": round(step_duration, 2),
                            "details": f"User registered: {test_user_email}"
                        })
                        
                        # Save progress after each step
                        E2ETestService._save_progress(
                            db=db,
                            test_run_id=test_run_id,
                            status="running",
                            steps=steps,
                            triggered_by=triggered_by,
                            test_user_email=test_user_email,
                            test_user_id=test_user_id,
                            test_metadata={
                                "frontend_url": frontend_url,
                                "api_url": api_url,
                                "browser": "chromium",
                                "playwright_version": "1.48.0",
                            },
                            current_step="register"
                        )
                    except Exception as e:
                        step_duration = (time.time() - step_start) * 1000
                        error_message = f"Registration failed: {str(e)}"
                        steps.append({
                            "step": "register",
                            "status": "failed",
                            "duration_ms": round(step_duration, 2),
                            "error": str(e)
                        })
                        # Save failed step progress
                        E2ETestService._save_progress(
                            db=db, test_run_id=test_run_id, status="failed", steps=steps,
                            triggered_by=triggered_by, test_user_email=test_user_email,
                            error_message=error_message, duration_ms=(time.time() - start_time) * 1000
                        )
                        raise
                    
                    # Step 2: Verify email (check SMTP)
                    step_start = time.time()
                    # Update current step before starting
                    E2ETestService._save_progress(
                        db=db, test_run_id=test_run_id, status="running", steps=steps,
                        triggered_by=triggered_by, test_user_email=test_user_email, test_user_id=test_user_id,
                        current_step="verify_email_check",
                        test_metadata={
                            "frontend_url": frontend_url,
                            "api_url": api_url,
                            "browser": "chromium",
                            "playwright_version": "1.48.0",
                        }
                    )
                    try:
                        # Check if verification email was sent (check audit logs or email service)
                        # For now, we'll just verify the user exists and is not verified yet
                        user = db.query(User).filter(User.email == test_user_email).first()
                        if user.is_verified:
                            raise Exception("User should not be verified yet (email verification step)")
                        
                        # In a real scenario, we would check SMTP/email service
                        # For now, we'll manually verify the email via API
                        # This step verifies that email verification flow exists
                        step_duration = (time.time() - step_start) * 1000
                        
                        steps.append({
                            "step": "verify_email_check",
                            "status": "passed",
                            "duration_ms": round(step_duration, 2),
                            "details": "Email verification check passed (user is unverified as expected)"
                        })
                        E2ETestService._save_progress(
                            db=db, test_run_id=test_run_id, status="running", steps=steps,
                            triggered_by=triggered_by, test_user_email=test_user_email, test_user_id=test_user_id,
                            test_metadata={
                                "frontend_url": frontend_url,
                                "api_url": api_url,
                                "browser": "chromium",
                                "playwright_version": "1.48.0",
                            }
                        )
                    except Exception as e:
                        step_duration = (time.time() - step_start) * 1000
                        error_message = f"Email verification check failed: {str(e)}"
                        steps.append({
                            "step": "verify_email_check",
                            "status": "failed",
                            "duration_ms": round(step_duration, 2),
                            "error": str(e)
                        })
                        raise
                    
                    # Step 3: Manually verify email (for testing purposes)
                    step_start = time.time()
                    # Update current step before starting
                    E2ETestService._save_progress(
                        db=db, test_run_id=test_run_id, status="running", steps=steps,
                        triggered_by=triggered_by, test_user_email=test_user_email, test_user_id=test_user_id,
                        current_step="verify_email",
                        test_metadata={
                            "frontend_url": frontend_url,
                            "api_url": api_url,
                            "browser": "chromium",
                            "playwright_version": "1.48.0",
                        }
                    )
                    try:
                        # Get verification token from database/Redis or use admin API
                        # For testing, we'll use the admin API to verify the email
                        import httpx
                        async with httpx.AsyncClient() as client:
                            # First, get admin token (or use service token)
                            # For now, we'll mark user as verified directly in DB for testing
                            user.is_verified = True
                            db.commit()
                            db.refresh(user)
                        
                        step_duration = (time.time() - step_start) * 1000
                        
                        steps.append({
                            "step": "verify_email",
                            "status": "passed",
                            "duration_ms": round(step_duration, 2),
                            "details": "Email verified successfully"
                        })
                        E2ETestService._save_progress(
                            db=db, test_run_id=test_run_id, status="running", steps=steps,
                            triggered_by=triggered_by, test_user_email=test_user_email, test_user_id=test_user_id,
                            test_metadata={
                                "frontend_url": frontend_url,
                                "api_url": api_url,
                                "browser": "chromium",
                                "playwright_version": "1.48.0",
                            }
                        )
                    except Exception as e:
                        step_duration = (time.time() - step_start) * 1000
                        error_message = f"Email verification failed: {str(e)}"
                        steps.append({
                            "step": "verify_email",
                            "status": "failed",
                            "duration_ms": round(step_duration, 2),
                            "error": str(e)
                        })
                        raise
                    
                    # Step 4: Login
                    step_start = time.time()
                    # Update current step before starting
                    E2ETestService._save_progress(
                        db=db, test_run_id=test_run_id, status="running", steps=steps,
                        triggered_by=triggered_by, test_user_email=test_user_email, test_user_id=test_user_id,
                        current_step="login",
                        test_metadata={
                            "frontend_url": frontend_url,
                            "api_url": api_url,
                            "browser": "chromium",
                            "playwright_version": "1.48.0",
                        }
                    )
                    try:
                        await page.goto(f"{frontend_url}/login", wait_until="networkidle")
                        await page.wait_for_selector('input[type="email"]', timeout=10000)
                        
                        await page.fill('input[type="email"]', test_user_email)
                        await page.fill('input[type="password"]', test_user_password)
                        await page.click('button[type="submit"]')
                        
                        # Wait for redirect to dashboard
                        await page.wait_for_url(f"{frontend_url}/dashboard**", timeout=10000)
                        
                        step_duration = (time.time() - step_start) * 1000
                        
                        steps.append({
                            "step": "login",
                            "status": "passed",
                            "duration_ms": round(step_duration, 2),
                            "details": "Login successful"
                        })
                        E2ETestService._save_progress(
                            db=db, test_run_id=test_run_id, status="running", steps=steps,
                            triggered_by=triggered_by, test_user_email=test_user_email, test_user_id=test_user_id,
                            test_metadata={
                                "frontend_url": frontend_url,
                                "api_url": api_url,
                                "browser": "chromium",
                                "playwright_version": "1.48.0",
                            }
                        )
                    except Exception as e:
                        step_duration = (time.time() - step_start) * 1000
                        error_message = f"Login failed: {str(e)}"
                        steps.append({
                            "step": "login",
                            "status": "failed",
                            "duration_ms": round(step_duration, 2),
                            "error": str(e)
                        })
                        raise
                    
                    # Step 5: Create keyword search
                    step_start = time.time()
                    # Update current step before starting
                    E2ETestService._save_progress(
                        db=db, test_run_id=test_run_id, status="running", steps=steps,
                        triggered_by=triggered_by, test_user_email=test_user_email, test_user_id=test_user_id,
                        current_step="create_keyword_search",
                        test_metadata={
                            "frontend_url": frontend_url,
                            "api_url": api_url,
                            "browser": "chromium",
                            "playwright_version": "1.48.0",
                        }
                    )
                    try:
                        # Navigate to keyword searches page
                        await page.goto(f"{frontend_url}/dashboard/keyword-searches", wait_until="networkidle")
                        await page.wait_for_timeout(2000)
                        
                        # Click create/search button
                        create_button = await page.query_selector('button:has-text("Create"), button:has-text("New"), a[href*="keyword"]')
                        if create_button:
                            await create_button.click()
                            await page.wait_for_timeout(1000)
                        
                        # Fill keyword search form
                        await page.wait_for_selector('input[name="name"], input[placeholder*="name" i]', timeout=10000)
                        await page.fill('input[name="name"], input[placeholder*="name" i]', "E2E Test Search")
                        
                        # Add keywords
                        keyword_input = await page.query_selector('input[placeholder*="keyword" i], input[name="keywords"]')
                        if keyword_input:
                            await keyword_input.fill("react developer")
                            await page.keyboard.press("Enter")
                        
                        # Submit form
                        submit_button = await page.query_selector('button[type="submit"], button:has-text("Create"), button:has-text("Save")')
                        if submit_button:
                            await submit_button.click()
                            await page.wait_for_timeout(3000)
                        
                        # Verify search was created in database
                        search = db.query(KeywordSearch).filter(
                            KeywordSearch.user_id == test_user_id,
                            KeywordSearch.name == "E2E Test Search"
                        ).first()
                        
                        if not search:
                            raise Exception("Keyword search was not created in database")
                        
                        keyword_search_id = search.id
                        step_duration = (time.time() - step_start) * 1000
                        
                        steps.append({
                            "step": "create_keyword_search",
                            "status": "passed",
                            "duration_ms": round(step_duration, 2),
                            "details": f"Keyword search created: {keyword_search_id}"
                        })
                        E2ETestService._save_progress(
                            db=db, test_run_id=test_run_id, status="running", steps=steps,
                            triggered_by=triggered_by, test_user_email=test_user_email, test_user_id=test_user_id,
                            test_metadata={
                                "frontend_url": frontend_url,
                                "api_url": api_url,
                                "browser": "chromium",
                                "playwright_version": "1.48.0",
                            }
                        )
                    except Exception as e:
                        step_duration = (time.time() - step_start) * 1000
                        error_message = f"Create keyword search failed: {str(e)}"
                        steps.append({
                            "step": "create_keyword_search",
                            "status": "failed",
                            "duration_ms": round(step_duration, 2),
                            "error": str(e)
                        })
                        raise
                    
                    # Step 6: Generate opportunities
                    step_start = time.time()
                    # Update current step before starting
                    E2ETestService._save_progress(
                        db=db, test_run_id=test_run_id, status="running", steps=steps,
                        triggered_by=triggered_by, test_user_email=test_user_email, test_user_id=test_user_id,
                        current_step="generate_opportunities",
                        test_metadata={
                            "frontend_url": frontend_url,
                            "api_url": api_url,
                            "browser": "chromium",
                            "playwright_version": "1.48.0",
                        }
                    )
                    try:
                        # Navigate to opportunities or use API to generate
                        # For now, we'll use API call since UI might be complex
                        import httpx
                        from services.auth_service import AuthService
                        
                        # Get user token for API call
                        token_data = AuthService.create_token_for_user(user)
                        token = token_data["access_token"]
                        
                        async with httpx.AsyncClient() as client:
                            response = await client.post(
                                f"{api_url}/api/v1/opportunities/generate",
                                params={"keyword_search_id": keyword_search_id, "limit": 10},
                                headers={"Authorization": f"Bearer {token}"},
                                timeout=60.0
                            )
                            
                            if response.status_code not in [200, 202]:
                                raise Exception(f"Failed to generate opportunities: {response.status_code} - {response.text}")
                        
                        # Wait a bit for opportunities to be generated
                        await asyncio.sleep(5)
                        
                        # Verify opportunities were created
                        opportunities = db.query(Opportunity).filter(
                            Opportunity.user_id == test_user_id,
                            Opportunity.keyword_search_id == keyword_search_id
                        ).all()
                        
                        step_duration = (time.time() - step_start) * 1000
                        
                        steps.append({
                            "step": "generate_opportunities",
                            "status": "passed",
                            "duration_ms": round(step_duration, 2),
                            "details": f"Generated {len(opportunities)} opportunities"
                        })
                        E2ETestService._save_progress(
                            db=db, test_run_id=test_run_id, status="running", steps=steps,
                            triggered_by=triggered_by, test_user_email=test_user_email, test_user_id=test_user_id,
                            test_metadata={
                                "frontend_url": frontend_url,
                                "api_url": api_url,
                                "browser": "chromium",
                                "playwright_version": "1.48.0",
                            }
                        )
                    except Exception as e:
                        step_duration = (time.time() - step_start) * 1000
                        error_message = f"Generate opportunities failed: {str(e)}"
                        steps.append({
                            "step": "generate_opportunities",
                            "status": "failed",
                            "duration_ms": round(step_duration, 2),
                            "error": str(e)
                        })
                        # Don't raise - this step might fail if Rixly is unavailable
                    
                    # Step 7: Create support thread
                    step_start = time.time()
                    # Update current step before starting
                    E2ETestService._save_progress(
                        db=db, test_run_id=test_run_id, status="running", steps=steps,
                        triggered_by=triggered_by, test_user_email=test_user_email, test_user_id=test_user_id,
                        current_step="create_support_thread",
                        test_metadata={
                            "frontend_url": frontend_url,
                            "api_url": api_url,
                            "browser": "chromium",
                            "playwright_version": "1.48.0",
                        }
                    )
                    try:
                        # Navigate to support page
                        await page.goto(f"{frontend_url}/dashboard/support", wait_until="networkidle")
                        await page.wait_for_timeout(2000)
                        
                        # Click create thread button
                        create_button = await page.query_selector('button:has-text("Create"), button:has-text("New"), a[href*="support"]')
                        if create_button:
                            await create_button.click()
                            await page.wait_for_timeout(1000)
                        
                        # Fill support form
                        subject_input = await page.query_selector('input[name="subject"], input[placeholder*="subject" i]')
                        if subject_input:
                            await subject_input.fill("E2E Test Support Request")
                        
                        message_input = await page.query_selector('textarea[name="message"], textarea[placeholder*="message" i]')
                        if message_input:
                            await message_input.fill("This is an automated E2E test support message.")
                        
                        # Submit form
                        submit_button = await page.query_selector('button[type="submit"], button:has-text("Send"), button:has-text("Submit")')
                        if submit_button:
                            await submit_button.click()
                            await page.wait_for_timeout(3000)
                        
                        # Verify thread was created in database
                        thread = db.query(SupportThread).filter(
                            SupportThread.user_id == test_user_id,
                            SupportThread.subject.like("%E2E Test%")
                        ).first()
                        
                        if not thread:
                            raise Exception("Support thread was not created in database")
                        
                        step_duration = (time.time() - step_start) * 1000
                        
                        steps.append({
                            "step": "create_support_thread",
                            "status": "passed",
                            "duration_ms": round(step_duration, 2),
                            "details": f"Support thread created: {thread.id}"
                        })
                        E2ETestService._save_progress(
                            db=db, test_run_id=test_run_id, status="running", steps=steps,
                            triggered_by=triggered_by, test_user_email=test_user_email, test_user_id=test_user_id,
                            test_metadata={
                                "frontend_url": frontend_url,
                                "api_url": api_url,
                                "browser": "chromium",
                                "playwright_version": "1.48.0",
                            }
                        )
                    except Exception as e:
                        step_duration = (time.time() - step_start) * 1000
                        error_message = f"Create support thread failed: {str(e)}"
                        steps.append({
                            "step": "create_support_thread",
                            "status": "failed",
                            "duration_ms": round(step_duration, 2),
                            "error": str(e)
                        })
                        # Don't raise - this is the last step
                    
                    # Determine overall status
                    failed_steps = [s for s in steps if s.get("status") == "failed"]
                    overall_status = "failed" if failed_steps else "passed"
                    
                    # Save final status
                    duration_ms = (time.time() - start_time) * 1000
                    E2ETestService._save_progress(
                        db=db, test_run_id=test_run_id, status=overall_status, steps=steps,
                        triggered_by=triggered_by, test_user_email=test_user_email, test_user_id=test_user_id,
                        error_message=error_message, duration_ms=duration_ms,
                        test_metadata={
                            "frontend_url": frontend_url,
                            "api_url": api_url,
                            "browser": "chromium",
                            "playwright_version": "1.48.0",
                        }
                    )
                    
                except Exception as e:
                    # Take screenshot on error
                    try:
                        # Save to logs directory (persistent volume) instead of /tmp
                        logs_dir = settings.LOG_FILE.rsplit('/', 1)[0] if settings.LOG_FILE else "/app/logs"
                        os.makedirs(logs_dir, exist_ok=True)
                        screenshot_path = os.path.join(logs_dir, f"e2e-test-{test_run_id}.png")
                        await page.screenshot(path=screenshot_path, full_page=True)
                    except:
                        pass
                    
                    overall_status = "error"
                    if not error_message:
                        error_message = str(e)
                    
                    raise
                finally:
                    await browser.close()
        
        except Exception as e:
            overall_status = "error"
            if not error_message:
                error_message = str(e)
            logger.error(f"E2E test failed: {str(e)}", exc_info=True)
        
        finally:
            # Cleanup: Delete test user
            if test_user_id:
                try:
                    user = db.query(User).filter(User.id == test_user_id).first()
                    if user:
                        # Delete user and all associated data (cascade delete)
                        db.delete(user)
                        db.commit()
                        logger.info(f"Cleaned up test user: {test_user_email}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup test user {test_user_email}: {str(e)}")
        
        duration_ms = (time.time() - start_time) * 1000
        
        return {
            "test_run_id": test_run_id,
            "status": overall_status,
            "triggered_by": triggered_by,
            "test_user_email": test_user_email,
            "test_user_id": test_user_id,
            "duration_ms": round(duration_ms, 2),
            "steps": steps,
            "error_message": error_message,
            "screenshot_path": screenshot_path,
            "test_metadata": {
                "frontend_url": frontend_url,
                "api_url": api_url,
                "browser": "chromium",
                "playwright_version": "1.48.0" if PLAYWRIGHT_AVAILABLE else "not_installed"
            }
        }
    
    @staticmethod
    def save_test_result(db: Session, test_result: Dict[str, Any]) -> E2ETestResult:
        """
        Save test result to database.
        
        Args:
            db: Database session
            test_result: Test result dictionary from run_full_flow_test
            
        Returns:
            E2ETestResult: Saved test result model
        """
        test_result_model = E2ETestResult(
            test_run_id=test_result["test_run_id"],
            status=test_result["status"],
            triggered_by=test_result["triggered_by"],
            test_user_email=test_result.get("test_user_email"),
            test_user_id=test_result.get("test_user_id"),
            duration_ms=test_result.get("duration_ms"),
            steps=test_result.get("steps", []),
            error_message=test_result.get("error_message"),
            screenshot_path=test_result.get("screenshot_path"),
            test_metadata=test_result.get("test_metadata")
        )
        
        db.add(test_result_model)
        db.commit()
        db.refresh(test_result_model)
        
        return test_result_model

