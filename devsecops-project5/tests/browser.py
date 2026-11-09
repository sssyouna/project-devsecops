"""Optional UI check using Selenium and Chromium under xvfb-run."""
import base64
from pathlib import Path
import uuid

from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.remote_connection import RemoteConnection
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def main():
    sku = "BROWSER-" + uuid.uuid4().hex[:10]
    options = webdriver.ChromeOptions()
    options.binary_location = "/usr/lib/chromium/chromium"
    for argument in ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                     "--remote-debugging-pipe", "--disable-background-networking"]:
        options.add_argument(argument)
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    RemoteConnection.set_timeout(45)
    with webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=options) as browser:
        print("PASS browser started on virtual display", flush=True)
        browser.set_window_size(1280, 1100)
        browser.set_page_load_timeout(30)
        wait = WebDriverWait(browser, 20, ignored_exceptions=(StaleElementReferenceException,))
        find = lambda selector: browser.find_element(By.CSS_SELECTOR, selector)
        row_path = f"//tbody/tr[td[1]='{sku}']"
        browser.get("http://127.0.0.1:8080")
        wait.until(lambda _: "Connected to PostgreSQL" in find("#status").text)
        sku_input = find('input[name="sku"]')
        sku_input.send_keys("INVALID SKU")
        assert browser.execute_script("return arguments[0].validity.patternMismatch", sku_input)
        print("PASS browser SKU pattern rejects spaces", flush=True)
        for name, value in [("sku", sku), ("name", "Browser demonstration item"),
                            ("location", "Fictional browser shelf"), ("quantity", "8")]:
            field = find(f'input[name="{name}"]')
            field.clear()
            field.send_keys(value)
        find("#save").click()
        wait.until(EC.text_to_be_present_in_element((By.XPATH, row_path), "Browser demonstration item"))
        print("PASS browser create and table rendering", flush=True)
        find(f'button[aria-label="Edit {sku}"]').click()
        find('input[name="quantity"]').clear()
        find('input[name="quantity"]').send_keys("11")
        find("#save").click()
        wait.until(lambda _: browser.find_element(By.XPATH, row_path + "/td[4]").text == "11")
        browser.refresh()
        wait.until(lambda _: browser.find_element(By.XPATH, row_path + "/td[4]").text == "11")
        print("PASS browser edit and reload persistence", flush=True)
        find(f'button[aria-label="Delete {sku}"]').click()
        wait.until(EC.alert_is_present()).accept()
        wait.until(lambda _: not browser.find_elements(By.XPATH, row_path))
        print("PASS browser delete", flush=True)
        browser.execute_script("window.scrollTo(0, 0)")
        size = browser.execute_cdp_cmd("Page.getLayoutMetrics", {})["cssContentSize"]
        screenshot = browser.execute_cdp_cmd("Page.captureScreenshot", {
            "captureBeyondViewport": True,
            "clip": {"x": 0, "y": 0, "width": size["width"], "height": size["height"], "scale": 1},
        })
        (Path(__file__).resolve().parents[1] / "evidence/v1/frontend.png").write_bytes(base64.b64decode(screenshot["data"]))
        browser.set_window_size(390, 844)
        assert find("#save").is_displayed()
        assert browser.execute_script("return document.documentElement.scrollWidth <= window.innerWidth"), "Mobile page overflows"
        assert "error" not in find("#status").get_attribute("class").split()
        errors = [entry for entry in browser.get_log("browser") if entry["level"] == "SEVERE"]
        assert not errors, f"Browser errors: {errors}"
        print("PASS mobile viewport layout and no browser/application errors", flush=True)


if __name__ == "__main__":
    main()
