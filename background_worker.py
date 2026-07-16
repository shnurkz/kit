import os
import time
import logging
import sys
from logging.handlers import RotatingFileHandler

# Force UTF-8 encoding for standard output and error to avoid UnicodeEncodeError on Windows terminals
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

# Import core_updater (ensure virtual env is active)
try:
    import core_updater
except ImportError as e:
    print(f"Error: Could not import core_updater. Make sure you activated the virtual environment: {e}")
    sys.exit(1)

# Configure robust logging to both console (stdout) and local log file
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Clear existing handlers (configured in core_updater import) to avoid duplicate logs
for handler in list(root_logger.handlers):
    root_logger.removeHandler(handler)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Rotating file handler (5MB size limit, keeps 3 backup files)
file_handler = RotatingFileHandler("updater_log.txt", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
file_handler.setFormatter(formatter)
root_logger.addHandler(file_handler)

# Console/stdout handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
root_logger.addHandler(console_handler)

logger = logging.getLogger("background_worker")

# Configurations
# Trigger interval in hours. Can be overridden using environmental variable UPDATE_INTERVAL_HOURS.
# Default: 1.0 hour. Set to 24.0 for once a day.
INTERVAL_HOURS = float(os.environ.get("UPDATE_INTERVAL_HOURS", 1.0))
interval_seconds = int(INTERVAL_HOURS * 3600)

def run_update():
    logger.info("=== Starting scheduled update process ===")
    start_time = time.time()
    try:
        # Download and parse XML price list
        xml_df = core_updater.load_and_parse_xml()
        if xml_df.empty:
            logger.warning("No data was parsed from the XML feed. Sync aborted.")
            return

        logger.info(f"Feed parsed successfully: {len(xml_df)} products found with stock >= 2.")

        # Sync products database and zero out missing supplier SKUs
        # Passing db_skus=None instructs it to fetch current list from Supabase
        core_updater.sync_supplier_to_db(xml_df, db_skus=None)

        elapsed = time.time() - start_time
        logger.info(f"=== Scheduled update process completed successfully in {elapsed:.2f}s ===")
    except Exception as e:
        logger.error(f"Error occurred during background update execution: {e}", exc_info=True)

if __name__ == "__main__":
    logger.info(f"Headless background worker initiated. Interval: {INTERVAL_HOURS} hours ({interval_seconds}s).")
    
    # Execute immediately on start
    run_update()
    
    # Infinite scheduler loop
    while True:
        try:
            logger.info(f"Scheduler sleeping for {interval_seconds}s until next update.")
            time.sleep(interval_seconds)
            run_update()
        except KeyboardInterrupt:
            logger.info("Background worker service interrupted and stopped by user.")
            break
        except Exception as e:
            logger.error(f"Unexpected loop exception: {e}. Retrying loop in 60s...", exc_info=True)
            time.sleep(60)
