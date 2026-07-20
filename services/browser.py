"""
Browser automation core library - Playwright based
Supports CAS SSO login, cookie persistence, auto retry
"""
import os
import json
import time
from playwright.sync_api import sync_playwright, Page, Browser


class BrowserSession:
    def __init__(self, cookie_file=None, headless=True, user_data_dir=None):
        self.cookie_file = cookie_file or "ahs_cookies.json"
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def start(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        if self.user_data_dir:
            self.context = self.browser.new_context(
                storage_state=self.cookie_file if os.path.exists(self.cookie_file) else None,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        else:
            self.context = self.browser.new_context(
                storage_state=self.cookie_file if os.path.exists(self.cookie_file) else None,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        self.page = self.context.new_page()
        self.page.set_default_timeout(30000)

    def close(self):
        try:
            self.save_cookies()
        except:
            pass
        if self.page:
            self.page.close()
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def save_cookies(self):
        if self.context:
            storage = self.context.storage_state()
            with open(self.cookie_file, "w", encoding="utf-8") as f:
                json.dump(storage, f, ensure_ascii=False, indent=2)

    def load_cookies(self):
        if os.path.exists(self.cookie_file):
            try:
                with open(self.cookie_file, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
                if self.context:
                    self.context.add_cookies(cookies.get("cookies", []))
                return True
            except:
                return False
        return False

    def goto(self, url, wait_strategy="networkidle", max_retries=3):
        strategies = ["networkidle", "load", "domcontentloaded"]
        if wait_strategy in strategies:
            idx = strategies.index(wait_strategy)
            try_strategies = strategies[idx:]
        else:
            try_strategies = [wait_strategy]
        last_error = None
        for attempt in range(max_retries):
            for strategy in try_strategies:
                try:
                    self.page.goto(url, wait_until=strategy, timeout=30000)
                    return True
                except Exception as e:
                    last_error = e
                    continue
            if attempt < max_retries - 1:
                time.sleep(2)
        if last_error:
            raise last_error
        return False

    def is_cas_login_page(self):
        url = self.page.url
        return "sso.aihuishou.com" in url or "cas/login" in url

    def cas_login(self, username, password):
        try:
            self.page.wait_for_selector("input[name='username'], #username, input[type='text']", timeout=10000)
            self.page.fill("input[name='username'], #username", username)
            self.page.fill("input[name='password'], #password", password)
            self.page.click("button[type='submit'], .btn-submit, input[type='submit']")
            self.page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)
            self.save_cookies()
            return not self.is_cas_login_page()
        except Exception as e:
            print(f"CAS login failed: {e}")
            return False
