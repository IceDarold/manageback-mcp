"""Read-only real-browser layout/interaction check of the external portal."""
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from managebac_mcp.config import Settings
from managebac_mcp.managed import COOKIE, ManagedAccounts


def main():
    for line in Path("/etc/manageback-mcp/auth.env").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            os.environ[key] = value
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/opt/manageback-mcp/browsers"
    managed = ManagedAccounts(Settings(managebac_config_path=Path("/opt/manageback-mcp/config/managebac.yaml")))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.add_cookies([{"name": COOKIE, "value": managed.cookie(), "url": managed.origin,
            "secure": True, "httpOnly": True, "sameSite": "Lax"}])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{managed.origin}/settings?return_to=https%3A%2F%2Fmcp.archik.tech%2Fconnections%2Fruntime%253Atest%3Fauthorization%3Dcomplete")
        page.get_by_text("Пока нет аккаунтов.", exact=False).wait_for()
        page.get_by_role("button", name="Добавить аккаунт").click()
        assert page.get_by_role("dialog").is_visible()
        page.get_by_label("Пароль", exact=True).fill("not-submitted")
        page.get_by_role("button", name="Отмена").click()
        assert page.get_by_label("Пароль", exact=True).input_value() == ""
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        browser.close()
        print("Desktop/mobile portal, account dialog, password clearing, return link: OK")


if __name__ == "__main__":
    main()
