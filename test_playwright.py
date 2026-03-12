from playwright.sync_api import sync_playwright
from playwright_stealth import stealth
import time
import sys

def main():
    print("Starting Playwright Test...")
    try:
        with sync_playwright() as p:
            print("Launching Chromium (headless=False)...")
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            stealth(page)

            url = "https://kaspi.kz/shop/search/?text=100007333"
            print(f"Navigating to {url}...")
            page.goto(url)
            
            # Wait a moment for title to populate
            page.wait_for_timeout(3000)
            
            title = page.title()
            print(f"Page Title: {title}")
            
            print("Closing browser in 2 seconds...")
            time.sleep(2)
            browser.close()
            print("Test Completed Successfully.")

    except Exception as e:
        print(f"Error occurred:\\n{e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
