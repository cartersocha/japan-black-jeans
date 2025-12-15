#!/usr/bin/env python3
"""
Japan Blue Jeans Restock Watcher

Monitors a specific product page for restock availability and optionally
sends Discord notifications when status changes from NOT_BUYABLE to BUYABLE.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Constants
DEFAULT_URL = (
    "https://www.japanblue-jeans.com/en_US/archive/j414-14oz-black-classic-straight-selvedge-jeans/"
    "JBJE14145S_BLK.html?dwopt_JBJE14145A__BLK__28_hemming=HEMMING-01&"
    "dwvar_JBJE14145S__BLK_color=BLK&dwvar_JBJE14145S__BLK_size=28&"
    "pid=JBJE14145A_BLK_28&quantity=1"
)

DEFAULT_STATE_FILE = "restock_state.json"
MAX_RETRIES = 3
INITIAL_BACKOFF = 1  # seconds


def fetch_html(url: str, verbose: bool = False) -> str:
    """
    Fetch HTML content from URL with retry logic and exponential backoff.
    
    Args:
        url: URL to fetch
        verbose: Enable verbose logging
        
    Returns:
        HTML content as string
        
    Raises:
        requests.RequestException: If all retries fail
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    
    backoff = INITIAL_BACKOFF
    last_exception = None
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if verbose:
                print(f"Fetching URL (attempt {attempt}/{MAX_RETRIES})...", file=sys.stderr)
            
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            
            if verbose:
                print(f"Successfully fetched {len(response.text)} bytes", file=sys.stderr)
            
            return response.text
            
        except requests.RequestException as e:
            last_exception = e
            if attempt < MAX_RETRIES:
                if verbose:
                    print(f"Attempt {attempt} failed: {e}. Retrying in {backoff}s...", file=sys.stderr)
                time.sleep(backoff)
                backoff *= 2
            else:
                if verbose:
                    print(f"All {MAX_RETRIES} attempts failed", file=sys.stderr)
    
    raise last_exception


def get_buyable_status(html: str) -> Tuple[bool, str]:
    """
    Determine if the product is buyable based on page content.
    
    Args:
        html: HTML content of the product page
        
    Returns:
        Tuple of (buyable: bool, reason: str)
    """
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text().lower()
    
    # Check for "Out of Stock" text (case-insensitive)
    if "out of stock" in page_text:
        return False, "Out of Stock message found"
    
    # Try multiple selectors for add-to-cart buttons
    selectors = [
        'button[name="add-to-cart"]',
        'button[data-action*="add-to-cart"]',
        'button.add-to-cart',
        'button[class*="add-to-cart"]',
        'button[class*="addtocart"]',
        'input[name="add-to-cart"]',
        'input[type="submit"][value*="cart" i]',
        'a[class*="add-to-cart"]',
    ]
    
    # Also search for buttons with "Add to Cart" text
    all_buttons = soup.find_all(['button', 'input', 'a'])
    
    add_to_cart_found = False
    button_disabled = False
    
    # Check via CSS selectors first
    for selector in selectors:
        elements = soup.select(selector)
        if elements:
            add_to_cart_found = True
            for element in elements:
                # Check if button is disabled
                disabled_attr = element.get("disabled")
                aria_disabled = element.get("aria-disabled", "").lower() == "true"
                
                if disabled_attr is not None or aria_disabled:
                    button_disabled = True
                    break
            
            if button_disabled:
                break
    
    # If not found via selectors, check all buttons for "Add to Cart" text
    if not add_to_cart_found:
        for element in all_buttons:
            button_text = element.get_text().lower()
            # Check if it's an add-to-cart button by text
            if "add to cart" in button_text or (element.name == "input" and "cart" in element.get("value", "").lower()):
                add_to_cart_found = True
                # Check if disabled
                disabled_attr = element.get("disabled")
                aria_disabled = element.get("aria-disabled", "").lower() == "true"
                
                if disabled_attr is not None or aria_disabled:
                    button_disabled = True
                break
    
    # If we found an enabled add-to-cart button, prioritize that
    if add_to_cart_found and not button_disabled:
        return True, "Add-to-cart button found and enabled"
    
    # Check for "Please select the product option(s)" message
    # (only if button is disabled or not found)
    if "please select the product option(s)" in page_text:
        return False, "Product options required but not selected"
    
    if add_to_cart_found and button_disabled:
        return False, "Add-to-cart button found but disabled"
    
    # If no add-to-cart button found, check if page loaded correctly
    if len(page_text) < 100:
        return False, "Page content too short (possible error page)"
    
    # Default to NOT_BUYABLE if we can't find add-to-cart button
    return False, "Add-to-cart button not found"


def load_state(state_file: str) -> dict:
    """
    Load state from JSON file.
    
    Args:
        state_file: Path to state file
        
    Returns:
        State dictionary with default values if file doesn't exist
    """
    if not os.path.exists(state_file):
        return {
            "last_status": None,
            "last_checked_at": None,
            "last_notified_at": None,
        }
    
    try:
        with open(state_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        # Return default state if file is corrupted
        return {
            "last_status": None,
            "last_checked_at": None,
            "last_notified_at": None,
        }


def save_state(state_file: str, status: str, notified: bool = False):
    """
    Save current state to JSON file.
    
    Args:
        state_file: Path to state file
        status: Current status ("BUYABLE" or "NOT_BUYABLE")
        notified: Whether notification was sent
    """
    state = {
        "last_status": status,
        "last_checked_at": datetime.now().isoformat(),
        "last_notified_at": datetime.now().isoformat() if notified else None,
    }
    
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def send_discord_notification(webhook_url: str, url: str, reason: str, verbose: bool = False) -> bool:
    """
    Send Discord notification via webhook.
    
    Args:
        webhook_url: Discord webhook URL
        url: Product URL
        reason: Reason for notification
        verbose: Enable verbose logging
        
    Returns:
        True if notification sent successfully, False otherwise
    """
    try:
        content = (
            f"🛒 **Japan Blue Jeans Restock Alert**\n\n"
            f"**Status:** BUYABLE\n"
            f"**Reason:** {reason}\n"
            f"**URL:** {url}"
        )
        
        payload = {"content": content}
        
        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        
        if verbose:
            print("Discord notification sent successfully", file=sys.stderr)
        
        return True
        
    except requests.RequestException as e:
        if verbose:
            print(f"Failed to send Discord notification: {e}", file=sys.stderr)
        return False


def maybe_notify(
    previous_status: Optional[str],
    current_status: str,
    webhook_url: Optional[str],
    url: str,
    reason: str,
    dry_run: bool,
    verbose: bool,
) -> bool:
    """
    Send notification if status transitioned from NOT_BUYABLE to BUYABLE.
    
    Args:
        previous_status: Previous status or None
        current_status: Current status
        webhook_url: Discord webhook URL or None
        url: Product URL
        reason: Reason for current status
        dry_run: If True, don't actually send notifications
        verbose: Enable verbose logging
        
    Returns:
        True if notification was sent (or would be sent in dry-run), False otherwise
    """
    # Only notify on transition from NOT_BUYABLE to BUYABLE
    if previous_status == "NOT_BUYABLE" and current_status == "BUYABLE":
        if not webhook_url:
            if verbose:
                print("Status changed to BUYABLE but DISCORD_WEBHOOK_URL not set", file=sys.stderr)
            return False
        
        if dry_run:
            if verbose:
                print(f"[DRY RUN] Would send notification: {reason}", file=sys.stderr)
            return True
        
        return send_discord_notification(webhook_url, url, reason, verbose)
    
    return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Monitor Japan Blue Jeans product page for restock availability"
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Product URL to monitor (default: {DEFAULT_URL[:50]}...)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Never send notifications (default: False)",
    )
    parser.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help=f"Path to state file (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging",
    )
    
    args = parser.parse_args()
    
    # Get Discord webhook URL from environment
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    
    if args.verbose and not webhook_url:
        print("DISCORD_WEBHOOK_URL not set; notifications disabled", file=sys.stderr)
    
    try:
        # Load previous state
        state = load_state(args.state_file)
        previous_status = state.get("last_status")
        
        if args.verbose:
            print(f"Previous status: {previous_status}", file=sys.stderr)
        
        # Fetch and analyze page
        html = fetch_html(args.url, args.verbose)
        buyable, reason = get_buyable_status(html)
        
        # Determine status string
        current_status = "BUYABLE" if buyable else "NOT_BUYABLE"
        
        # Print result (exactly one line)
        print(f"{current_status} - {reason}")
        
        # Check if we should notify
        notified = maybe_notify(
            previous_status,
            current_status,
            webhook_url,
            args.url,
            reason,
            args.dry_run,
            args.verbose,
        )
        
        # Save state
        save_state(args.state_file, current_status, notified)
        
        # Exit with appropriate code
        sys.exit(0 if buyable else 1)
        
    except requests.RequestException as e:
        print(f"NOT_BUYABLE - Network error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"NOT_BUYABLE - Unexpected error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

