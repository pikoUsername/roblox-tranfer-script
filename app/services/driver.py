from typing import List, Dict, Any, TYPE_CHECKING
from urllib.parse import urlparse

from loguru import logger
from selenium.common import StaleElementReferenceException
from selenium.webdriver.firefox.service import Service as GeckoService
from selenium.webdriver.remote.webdriver import WebDriver
from seleniumwire import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.firefox import GeckoDriverManager

if TYPE_CHECKING:
    from app.settings import Settings


def csrf_token_to_request(csrf_token: str, roblox_token: str):
    def interceptor(request):
        request.headers['X-CSRF-TOKEN'] = csrf_token
        request.headers['Cookies'] = ".ROBLOSECURITY=" + roblox_token

    return interceptor


def set_token(driver: WebDriver, token: str) -> None:
    driver.add_cookie({"name": ".ROBLOSECURITY", "value": token, "domain": ".roblox.com", "secure": True, "httponly": True})


def get_driver(settings: "Settings") -> WebDriver:
    logger.info("Setting up driver")

    if settings.browser.lower() == "chrome":
        opts = webdriver.ChromeOptions()
        agent = settings.user_agent
        opts.add_argument(agent)
        opts.add_argument('--ignore-ssl-errors=yes')
        opts.add_argument('--disable-gpu')
        opts.add_argument('--ignore-certificate-errors')
        opts.add_argument("--disable-dev-shm-usage")
        # opts.add_argument("--headless")
        # opts.add_argument("--window-size=%s" % settings.window_size)
        # opts.add_argument("--blink-settings=imagesEnabled=false")
        opts.add_argument('--no-sandbox')
        opts.add_argument("--log-level=3")

        service = ChromeService(executable_path="./drivers/chromedriver.exe")

        driver = webdriver.Chrome(service=service, options=opts)

    elif settings.browser.lower() == "remote":
        logger.info("Setting up remote chrome browser")

        opts = webdriver.ChromeOptions()
        agent = settings.user_agent
        opts.add_argument(agent)
        opts.add_argument('--ignore-ssl-errors=yes')
        opts.add_argument('--disable-gpu')
        opts.add_argument('--ignore-certificate-errors')
        opts.add_argument("--headless")
        opts.add_argument("--window-size=%s" % settings.window_size)
        opts.add_argument("--log-level=3")
        logger.info(f"Options of browser: {opts.arguments}")

        logger.info(f"Connecting to {settings.browser_dsn} Remote browser")

        driver = webdriver.Remote(command_executor=settings.browser_dsn, options=opts)
    elif settings.browser.lower() == "firefox" or settings.browser.lower() == "gecko":
        logger.info("Setting up remote firefox browser")

        opts = webdriver.FirefoxOptions()
        opts.add_argument("--disable-web-security")
        if not settings.debug:
            opts.add_argument("--headless")
            opts.add_argument('--disable-gpu')
        agent = settings.user_agent
        opts.add_argument(agent)

        service = GeckoService(GeckoDriverManager(path="./drivers/").install())
        driver = webdriver.Firefox(service=service, options=opts)
    else:
        raise NotImplementedError(f"{settings.browser} is not yet implemented")

    return driver


def convert_browser_cookies_to_aiohttp(cookies: List[Dict[str, Any]]) -> Dict[str, Any]:
    result = {}
    for cookie in cookies:
        name = cookie["name"]
        value = cookie["value"]
        result[name] = value
    return result


def extract_user_id_from_profile_url(url: str) -> int:
    uri = urlparse(url).path[1:]
    parts = uri.split('/')
    return int(parts[1])


def presence_of_any_text_in_element(locator):
    """
    It returns the text of the element

    :param locator:
    :return:
    """
    def _predicate(driver):
        try:
            element = driver.find_element(*locator)
            if element.text != "":
                return element
            return False
        except StaleElementReferenceException:
            return False

    return _predicate