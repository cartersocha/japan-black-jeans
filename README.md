# Japan Blue Jeans Restock Watcher

A local Python script that monitors a specific Japan Blue Jeans product page for restock availability and optionally sends Discord notifications when the item becomes available.

## Features

- Monitors product page for availability using robust heuristics
- Detects "Out of Stock" messages and disabled add-to-cart buttons
- Optional Discord webhook notifications (only on status change from NOT_BUYABLE → BUYABLE)
- Persistent state tracking via JSON file
- Retry logic with exponential backoff for network failures
- Minimal dependencies (requests + beautifulsoup4 only)

## Local Setup

### Prerequisites

- Python 3.11 or higher
- pip (Python package manager)

### Installation

1. Create a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:

```bash
pip install requests beautifulsoup4 python-dotenv
```

### Configuration

The script monitors this product by default:
- **Product:** J414 14oz Black Classic Straight Selvedge Jeans (Size 28)
- **URL:** See `DEFAULT_URL` in `japanblue_restock_watch.py`

You can override the URL using the `--url` flag.

### Optional: Discord Notifications

To enable Discord notifications, create a `.env` file in the project root:

```bash
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN
```

Alternatively, you can set it as an environment variable:

```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN"
```

To create a Discord webhook:
1. Go to your Discord server settings
2. Navigate to Integrations → Webhooks
3. Create a new webhook and copy the webhook URL

## Usage

### Basic Usage (Dry Run)

Run the script without sending notifications:

```bash
python japanblue_restock_watch.py --dry-run
```

### With Discord Notifications

```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python japanblue_restock_watch.py
```

### Command-Line Options

- `--url URL`: Override the default product URL
- `--dry-run`: Never send notifications (default: False)
- `--state-file PATH`: Path to state file (default: `restock_state.json`)
- `--verbose`: Enable verbose debug logging

### Examples

```bash
# Check availability with verbose output
python japanblue_restock_watch.py --verbose

# Monitor a different product
python japanblue_restock_watch.py --url "https://www.japanblue-jeans.com/..."

# Use custom state file
python japanblue_restock_watch.py --state-file my_state.json
```

## Output

The script prints exactly one line per run:

- `BUYABLE - <reason>`: Product is available
- `NOT_BUYABLE - <reason>`: Product is not available

Exit codes:
- `0`: Product is buyable
- `1`: Product is not buyable or error occurred

## State File

The script maintains state in `restock_state.json` (or custom path via `--state-file`):

```json
{
  "last_status": "NOT_BUYABLE",
  "last_checked_at": "2025-12-14T14:23:00-08:00",
  "last_notified_at": null
}
```

Notifications are only sent when the status transitions from `NOT_BUYABLE` to `BUYABLE`.

## Testing

### Test Dry Run

```bash
python japanblue_restock_watch.py --dry-run --verbose
```

This will:
- Fetch the product page
- Analyze availability
- Print status and reason
- Save state (but not send notifications)

### Test with Mock Webhook

You can test the notification logic by setting a test webhook URL:

```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/test"
python japanblue_restock_watch.py --verbose
```

## How It Works

The script uses several heuristics to determine availability:

1. **Out of Stock Detection**: Checks for "Out of Stock" text (case-insensitive)
2. **Add-to-Cart Button**: Searches for add-to-cart buttons using multiple selectors:
   - `button[name="add-to-cart"]`
   - `button[data-action*="add-to-cart"]`
   - `button.add-to-cart`
   - And several other common patterns
3. **Button State**: Checks if buttons are disabled via `disabled` attribute or `aria-disabled="true"`
4. **Product Options**: Detects if product options need to be selected

## Troubleshooting

### Network Errors

The script includes automatic retry logic (3 attempts with exponential backoff). If you see persistent network errors:

- Check your internet connection
- Verify the URL is accessible
- Try running with `--verbose` to see detailed error messages

### False Positives/Negatives

If the script incorrectly detects availability:

1. Run with `--verbose` to see what the script found
2. Check the actual page HTML structure
3. The selectors may need adjustment if the site structure changes

### Discord Notifications Not Working

- Verify `DISCORD_WEBHOOK_URL` is set correctly
- Check that the webhook URL is valid and active
- Run with `--verbose` to see notification errors
- Ensure notifications only trigger on status transitions (NOT_BUYABLE → BUYABLE)

## Later: Automation

### GitHub Actions Cron

To automate this script, you can set up a GitHub Actions workflow that runs on a schedule:

1. **Create `.github/workflows/restock-watch.yml`**:

```yaml
name: Restock Watcher

on:
  schedule:
    - cron: '*/15 * * * *'  # Every 15 minutes
  workflow_dispatch:  # Allow manual runs

jobs:
  watch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install requests beautifulsoup4
      - name: Run watcher
        env:
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
        run: |
          python japanblue_restock_watch.py
```

2. **Set up Secrets**:
   - Go to your repository Settings → Secrets and variables → Actions
   - Add `DISCORD_WEBHOOK_URL` as a secret

3. **State Persistence Options**:

   **Option A: Cache (Recommended)**
   - Use `actions/cache@v3` to persist the state file between runs
   - Cache key: `restock-state-${github.run_id}`
   - Restore cache before running, save after

   **Option B: Commit State**
   - Commit `restock_state.json` to the repository
   - Checkout before running, commit changes after
   - Note: This creates commit noise

   **Option C: External Storage**
   - Store state in GitHub Gist, S3, or similar
   - Fetch before running, upload after

### Recommended Cron Schedule

- **Frequent**: `*/15 * * * *` (every 15 minutes)
- **Moderate**: `*/30 * * * *` (every 30 minutes)
- **Conservative**: `0 * * * *` (hourly)

Adjust based on:
- How quickly you need to know about restocks
- GitHub Actions minutes usage limits
- Site rate limiting considerations

## License

This script is provided as-is for personal use.

