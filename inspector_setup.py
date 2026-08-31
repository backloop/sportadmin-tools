import re
from playwright.sync_api import Playwright, sync_playwright, expect

from credentials import load_credentials


def run(playwright: Playwright) -> None:
    email, password = load_credentials()

    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://www.sportadmin.se/")
    page.get_by_role("button", name="Navigation Menu").click()
    page.get_by_label("Off Canvas Menu").get_by_role("link", name="Logga in").click()
    page.goto("https://identity.sportadmin.se/identity/account/login")
    page.locator("#loginemail").click()
    page.locator("#loginemail").fill(email)
    page.locator("#loginemail").press("Tab")
    page.locator("#loginpass").fill(password)
    page.get_by_role("button", name="Log in").click()
    page.goto("https://kansli.sportadmin.se/")
    #page.get_by_role("link", name="Matcher").click()

    # ---------------------
    #context.close()
    #browser.close()


with sync_playwright() as playwright:
    run(playwright)
