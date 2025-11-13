"""
Input Sanitization Utilities

Provides functions to sanitize user input to prevent XSS attacks.
"""

import bleach
from html import escape
from typing import Optional
from core.logger import get_logger

logger = get_logger(__name__)

# Maximum lengths for different input types
MAX_SUBJECT_LENGTH = 255
MAX_MESSAGE_LENGTH = 10000
MAX_NOTES_LENGTH = 5000
MAX_NAME_LENGTH = 255


def sanitize_text(
    text: str, 
    max_length: Optional[int] = None,
    allow_html: bool = False
) -> str:
    """
    Sanitize user input text to prevent XSS attacks.
    
    Args:
        text: Input text to sanitize
        max_length: Maximum allowed length (truncates if longer)
        allow_html: Whether to allow HTML tags (default: False, strips all HTML)
        
    Returns:
        Sanitized text
        
    Security:
        - Strips all HTML tags by default
        - Escapes special characters
        - Truncates if exceeds max_length
        - Removes null bytes and control characters
    """
    if not text:
        return ""
    
    # Remove null bytes and control characters (except newlines and tabs)
    text = ''.join(char for char in text if ord(char) >= 32 or char in '\n\t')
    
    # Truncate if too long
    if max_length and len(text) > max_length:
        text = text[:max_length]
        logger.warning(f"Input truncated to {max_length} characters")
    
    # Strip whitespace
    text = text.strip()
    
    if allow_html:
        # Allow only safe HTML tags (if needed in future)
        # For now, we don't allow HTML in user input
        ALLOWED_TAGS = []
        ALLOWED_ATTRIBUTES = {}
        cleaned = bleach.clean(text, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, strip=True)
    else:
        # Strip all HTML tags and escape
        cleaned = bleach.clean(text, tags=[], attributes={}, strip=True)
    
    return cleaned


def sanitize_subject(subject: str) -> str:
    """
    Sanitize support thread subject.
    
    Args:
        subject: Thread subject
        
    Returns:
        Sanitized subject (max 255 characters)
    """
    return sanitize_text(subject, max_length=MAX_SUBJECT_LENGTH, allow_html=False)


def sanitize_message(message: str) -> str:
    """
    Sanitize support message content.
    
    Args:
        message: Message content
        
    Returns:
        Sanitized message (max 10000 characters)
    """
    return sanitize_text(message, max_length=MAX_MESSAGE_LENGTH, allow_html=False)


def sanitize_notes(notes: str) -> str:
    """
    Sanitize opportunity notes.
    
    Args:
        notes: Opportunity notes
        
    Returns:
        Sanitized notes (max 5000 characters)
    """
    return sanitize_text(notes, max_length=MAX_NOTES_LENGTH, allow_html=False)


def sanitize_name(name: str) -> str:
    """
    Sanitize user name or other name fields.
    
    Args:
        name: Name to sanitize
        
    Returns:
        Sanitized name (max 255 characters)
    """
    return sanitize_text(name, max_length=MAX_NAME_LENGTH, allow_html=False)


def sanitize_extracted_info(extracted_info: dict | None) -> dict | None:
    """
    Sanitize extracted_info to only include fields needed by frontend.
    
    Removes internal AI processing structure (classification, contact_info, budget_info, scores)
    and only returns user-facing fields.
    
    Args:
        extracted_info: Raw extracted_info dict from database
        
    Returns:
        Sanitized dict with only frontend-required fields, or None if input is None/empty
        
    Security:
        - Prevents leaking internal AI processing structure
        - Only exposes fields actually used by frontend
        - Removes confidence scores, reasoning, internal metadata
    """
    if not extracted_info or not isinstance(extracted_info, dict):
        return None
    
    # Fields that frontend actually uses (from frontend/src/types/opportunity.ts and BudgetDisplay.tsx)
    allowed_fields = {
        # Budget fields
        'budget',
        'budget_min',
        'budget_max',
        'budget_currency',
        # Timeline fields
        'timeline',
        'deadline',
        # Requirements and skills
        'requirements',
        'skills',
        # Location fields
        'location',
        'remote',
        # Payment method
        'payment_method',
    }
    
    # Build sanitized dict with only allowed fields
    sanitized = {}
    for field in allowed_fields:
        if field in extracted_info:
            value = extracted_info[field]
            # Only include non-null values
            if value is not None:
                sanitized[field] = value
    
    # Return None if no valid fields found (instead of empty dict)
    return sanitized if sanitized else None

