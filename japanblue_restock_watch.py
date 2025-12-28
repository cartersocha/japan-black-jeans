#!/usr/bin/env python3
"""
Japan Blue Jeans Restock Watcher

Monitors a specific product page for restock availability and optionally
sends Discord notifications when status changes from NOT_BUYABLE to BUYABLE.
"""

import argparse
import json
import os
import re
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
PRODUCTS = [
    {
        "name": "Japan Blue Jeans - Size 28",
        "url": (
            "https://www.japanblue-jeans.com/en_US/archive/j414-14oz-black-classic-straight-selvedge-jeans/"
            "JBJE14145S_BLK.html?dwopt_JBJE14145A__BLK__28_hemming=HEMMING-01&"
            "dwvar_JBJE14145S__BLK_color=BLK&dwvar_JBJE14145S__BLK_size=28&"
            "pid=JBJE14145A_BLK_28&quantity=1"
        ),
        "type": "japanblue"
    },
    {
        "name": "Marco Polo Tea - 8oz",
        "url": "https://www.theculturedcup.com/products/marco-polo-by-mariage-freres-1?variant=27890666602598",
        "type": "shopify"
    }
]

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


def get_buyable_status(html: str, product_type: str = "japanblue", url: str = "") -> Tuple[bool, str]:
    """
    Determine if the product is buyable based on page content.
    
    Args:
        html: HTML content of the product page
        product_type: Type of product site ("japanblue" or "shopify")
        
    Returns:
        Tuple of (buyable: bool, reason: str)
    """
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text().lower()
    html_lower = html.lower()
    
    # Shopify-specific detection (The Cultured Cup)
    if product_type == "shopify":
        # Extract variant ID from URL if present
        variant_id = None
        if "variant=" in url:
            try:
                variant_id = url.split("variant=")[1].split("&")[0].split("#")[0]
            except:
                pass
        
        # Check for variant-specific availability in JSON data
        # Shopify stores product data in JSON-LD or script tags
        
        # Look for variant data in various formats
        # Pattern 1: "variants":[...] with availability info
        variant_patterns = [
            r'"variants"\s*:\s*\[([^\]]+)\]',
            r'"variant"\s*:\s*\{([^}]+)\}',
        ]
        
        # Check for the specific variant's availability
        if variant_id:
            # Look for variant ID in the HTML with availability info
            # Common patterns: "id":27890666602598 with "available":false
            variant_available_pattern = rf'"id"\s*:\s*{variant_id}[^}}]*"available"\s*:\s*(true|false)'
            variant_inventory_pattern = rf'"id"\s*:\s*{variant_id}[^}}]*"inventory_quantity"\s*:\s*(\d+)'
            variant_available_text_pattern = rf'{variant_id}[^<]*"available"[^<]*false'
            
            # Check if variant is marked as unavailable
            if re.search(variant_available_pattern, html, re.IGNORECASE):
                match = re.search(variant_available_pattern, html, re.IGNORECASE)
                if match and match.group(1).lower() == "false":
                    return False, f"Variant {variant_id} marked as unavailable"
            
            # Check inventory quantity
            if re.search(variant_inventory_pattern, html, re.IGNORECASE):
                match = re.search(variant_inventory_pattern, html, re.IGNORECASE)
                if match and int(match.group(1)) == 0:
                    return False, f"Variant {variant_id} has zero inventory"
            
            # Check for "available":false near variant ID (but be more specific)
            # Only match if it's in a proper JSON structure
            variant_json_pattern = rf'{{[^}}]*"id"\s*:\s*{variant_id}[^}}]*"available"\s*:\s*false[^}}]*}}'
            if re.search(variant_json_pattern, html, re.IGNORECASE):
                return False, f"Variant {variant_id} marked as unavailable in JSON"
        
        # Check for general "out of stock" indicators for the selected variant
        # Look for "Out of stock" text that appears after variant selection
        # But only if it's clearly for the selected variant
        if variant_id:
            # Check if the variant ID appears with "out of stock":true
            variant_oot_pattern = rf'{variant_id}[^}}]*"out of stock"\s*:\s*true'
            if re.search(variant_oot_pattern, html_lower):
                return False, f"Variant {variant_id} marked as out of stock"
        
        # Check for "Out of stock" text in the visible page content
        # But be more careful - only flag if it's clearly for the selected variant
        # Skip this check if we have variant-specific data above
        
        # Check for disabled add to cart buttons in Shopify
        add_to_cart_disabled = False
        # Look for disabled buttons with various selectors
        disabled_selectors = [
            'button[disabled]',
            'button[disabled="disabled"]',
            'input[type="submit"][disabled]',
            '[class*="disabled"][class*="cart"]',
        ]
        for selector in disabled_selectors:
            if soup.select(selector):
                add_to_cart_disabled = True
                break
        
        # Also check for "Sold out" text which is common in Shopify
        if "sold out" in page_text.lower():
            return False, "Sold out message found"
        
        if add_to_cart_disabled and 'add to cart' in page_text:
            return False, "Add-to-cart button disabled"
        
        # If we get here and page loaded, check for positive availability indicators
        if len(page_text) > 100:
            # Look for positive indicators like "Add to cart" button that's enabled
            add_to_cart_buttons = soup.find_all(['button', 'input'], string=re.compile('add to cart', re.I))
            enabled_button_found = False
            if add_to_cart_buttons:
                # Check if any button is not disabled
                for btn in add_to_cart_buttons:
                    if not btn.get('disabled') and not btn.get('aria-disabled'):
                        enabled_button_found = True
                        break
            
            # If we found an enabled add-to-cart button, assume buyable
            if enabled_button_found:
                return True, "Add-to-cart button found and enabled (Shopify)"
            
            # If variant ID was checked and we didn't find negative indicators, assume available
            # (This handles cases where the variant is available but detection patterns didn't match)
            if variant_id:
                # We checked for negative indicators above, if none found and page loaded, assume available
                return True, f"Variant {variant_id} appears available (no negative indicators found)"
            
            # Default to NOT_BUYABLE if we can't determine
            return False, "Unable to confirm availability (Shopify - no clear indicators)"
        else:
            return False, "Page content too short"
    
    # Japan Blue Jeans detection (original logic)
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
        State dictionary with per-product status tracking
    """
    if not os.path.exists(state_file):
        return {"products": {}}
    
    try:
        with open(state_file, "r") as f:
            state = json.load(f)
            # Migrate old format to new format if needed
            if "last_status" in state and "products" not in state:
                # Old format - migrate to new format
                return {"products": {}}
            return state
    except (json.JSONDecodeError, IOError) as e:
        # Return default state if file is corrupted
        return {"products": {}}


def save_state(state_file: str, product_name: str, status: str, notified: bool = False):
    """
    Save current state to JSON file.
    
    Args:
        state_file: Path to state file
        product_name: Name/identifier of the product
        status: Current status ("BUYABLE" or "NOT_BUYABLE")
        notified: Whether notification was sent
    """
    state = load_state(state_file)
    
    if "products" not in state:
        state["products"] = {}
    
    state["products"][product_name] = {
        "last_status": status,
        "last_checked_at": datetime.now().isoformat(),
        "last_notified_at": datetime.now().isoformat() if notified else None,
    }
    
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def send_discord_notification(webhook_url: str, product_name: str, url: str, reason: str, verbose: bool = False) -> bool:
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
            f"🛒 **Restock Alert: {product_name}**\n\n"
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
    product_name: str,
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
        product_name: Name of the product
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
                print(f"[DRY RUN] Would send notification for {product_name}: {reason}", file=sys.stderr)
            return True
        
        return send_discord_notification(webhook_url, product_name, url, reason, verbose)
    
    return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Monitor product pages for restock availability"
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Override: monitor a single URL instead of all products",
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
    
    # Load previous state
    state = load_state(args.state_file)
    
    # Determine which products to check
    if args.url:
        # Single URL override mode (backward compatibility)
        products_to_check = [{"name": "Custom URL", "url": args.url, "type": "japanblue"}]
    else:
        # Check all configured products
        products_to_check = PRODUCTS
    
    all_successful = True
    
    # Check each product
    for product in products_to_check:
        product_name = product["name"]
        product_url = product["url"]
        product_type = product.get("type", "japanblue")
        
        try:
            if args.verbose:
                print(f"\nChecking {product_name}...", file=sys.stderr)
            
            # Get previous status for this product
            product_state = state.get("products", {}).get(product_name, {})
            previous_status = product_state.get("last_status")
            
            if args.verbose:
                print(f"Previous status: {previous_status}", file=sys.stderr)
            
            # Fetch and analyze page
            html = fetch_html(product_url, args.verbose)
            buyable, reason = get_buyable_status(html, product_type, product_url)
            
            # Determine status string
            current_status = "BUYABLE" if buyable else "NOT_BUYABLE"
            
            # Print result (one line per product)
            print(f"{product_name}: {current_status} - {reason}")
            
            # Check if we should notify
            notified = maybe_notify(
                previous_status,
                current_status,
                webhook_url,
                product_name,
                product_url,
                reason,
                args.dry_run,
                args.verbose,
            )
            
            # Save state for this product
            save_state(args.state_file, product_name, current_status, notified)
            
        except requests.RequestException as e:
            print(f"{product_name}: NOT_BUYABLE - Network error: {e}", file=sys.stderr)
            all_successful = False
        except Exception as e:
            print(f"{product_name}: NOT_BUYABLE - Unexpected error: {e}", file=sys.stderr)
            if args.verbose:
                import traceback
                traceback.print_exc()
            all_successful = False
    
    # Exit with success code (0) if all checks completed
    # Only exit with error code (1) for actual failures
    sys.exit(0 if all_successful else 1)


if __name__ == "__main__":
    main()

