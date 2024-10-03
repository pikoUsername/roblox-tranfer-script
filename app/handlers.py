import asyncio
import json
from typing import Optional

import pydantic
from aiohttp import ClientSession
from loguru import logger
from selenium.common import NoSuchElementException, TimeoutException
from selenium.webdriver import Chrome
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.wait import WebDriverWait

from app.browser import is_authed
from app.settings import Settings
from app.services.interfaces import IListener, BasicDBConnector
from app.services.db import get_db_conn
from app.services.driver import set_token, convert_browser_cookies_to_aiohttp, get_driver, \
    presence_of_any_text_in_element
from app.repos import TokenRepository
from app.consts import ROBLOX_TOKEN_KEY, TOKEN_RECURSIVE_CHECK, ROBLOX_HOME_URL
from app.services.exceptions import CancelException
from app.services.queue.publisher import BasicMessageSender
from app.schemas import ReturnSignal, StatusCodes, SendError
from app.schemas import PurchaseData
from app.services.validators import validate_game_pass_url


def press_agreement_button(browser: Chrome):
    try:
        logger.info("Pressing user agreement button")
        btn = browser.find_element(By.CSS_SELECTOR, ".modal-window .modal-footer .modal-button")
        btn.click()
    except NoSuchElementException:
        return


class UrlHandler(IListener):
    """
    Основной хендлер всех запросов,

    Он должен иметь в __init__ только самое необходимое!

    Очень грязный код
    """
    def __init__(self) -> None:
        self.config: Optional[Settings] = None

        self.token_service: Optional[TokenRepository] = None
        self.setupped = False

    async def setup(self):
        pass

    def close(self):
        pass

    async def get_robuxes(self, driver: Chrome, session: ClientSession) -> int:
        try:
            # if it's text is ? then it means we cant buy, it means this session can't be used
            text = WebDriverWait(driver, 3).until(
                presence_of_any_text_in_element((By.ID, "nav-robux-amount"))
            )
            text = int(text.text)
        except TimeoutException:
            return await self.get_robux_by_request(driver, session)

        return text

    async def get_robux_by_request(self, driver: Chrome, session: ClientSession) -> int:
        cookies = driver.get_cookies()
        cookies = convert_browser_cookies_to_aiohttp(cookies)

        element = driver.find_element(By.CSS_SELECTOR, "meta[name='user-data']")
        user_id = element.get_attribute("data-userid")

        robux_url = "https://economy.roblox.com/v1/users/{user_id}/currency"

        async with session.get(robux_url.format(user_id=user_id), cookies=cookies) as resp:
            logger.info(f"Headers, {resp.headers}")
            logger.info(f"Status, {resp.status}")
            logger.info(f"Body, {await resp.text()}")

            assert resp.status == 200

            return (await resp.json()).get("robux")

    async def mark_as_spent(self, driver) -> None:
        token = driver.get_cookie(ROBLOX_TOKEN_KEY)
        await self.token_service.mark_as_inactive(token)

    async def change_token(self, driver) -> None:
        # marks the current token as spent
        await self.mark_as_spent(driver)
        driver.delete_cookie(name=ROBLOX_TOKEN_KEY)
        tokens = await self.token_service.fetch_selected_tokens()
        if not tokens:
            tokens = await self.token_service.fetch_active_tokens()
            await self.token_service.mark_as_selected(tokens[0])
            if not tokens:
                logger.info("OUT OF TOKENS")
                return
        token = tokens[0]
        set_token(driver, token)
        driver.refresh()

    def get_driver_token(self, driver) -> str:
        return driver.get_cookie(name=ROBLOX_TOKEN_KEY)

    async def change_token_recursive(self, driver: Chrome, depth: int = TOKEN_RECURSIVE_CHECK):
        if depth == 0:
            raise RuntimeError("TOKENS CORRUPTED, WAITING FOR ACTIONS")
        await self.change_token(driver)
        if not is_authed(driver):
            await self.change_token(driver)
        await self.change_token_recursive(driver, depth - 1)

    async def change_token_to_selected(self, driver: Chrome):
        driver.delete_cookie(name=ROBLOX_TOKEN_KEY)
        tokens = await self.token_service.fetch_selected_tokens()
        if not tokens:
            tokens = await self.token_service.fetch_active_tokens()
            await self.token_service.mark_as_selected(tokens[0])
            if not tokens:
                raise RuntimeError("No tokens available")
        token = tokens[0]
        set_token(driver, token)
        driver.refresh()

    async def __call__(
            self,
            driver: Chrome,
            purchase_data: PurchaseData,
            settings: Settings,
            publisher: BasicMessageSender,
            data: dict,
            session: ClientSession
    ) -> None:
        # if not validate_game_pass_url(purchase_data.url):
        #     logger.info("Not correct url, denying!")
        #     data.update(
        #         return_signal=ReturnSignal(status_code=StatusCodes.invalid_data)
        #     )
        #     returnц

        logger.info(f"Redirecting to {purchase_data.url}")
        driver.get(purchase_data.url)

        try:
            robux = await self.get_robuxes(driver, session)
        except ValueError:
            # не надо волноватся если транзакция улетит в утиль
            # потому как здесь если обработка сообщения прервана
            # и без ack то реббитмкью не удалит ту запись
            logger.error("ROBLOX DETECTED WEB BROWSER IS A BOT. RESTARTING!")
            raise

        if settings.debug:
            driver.save_screenshot("screenshot.png")

        cost = driver.find_element(By.CLASS_NAME, "text-robux-lg")
        logger.info(f"Cost of gamepass from page: {cost.text}")
        if purchase_data.price != int(cost.text.replace(",", "")):
            logger.info("Price is not equal to url's price")

            data.update(
                return_signal=ReturnSignal(status_code=StatusCodes.invalid_price)
            )

            return
        if not await self.token_service.is_token_selected(self.get_driver_token(driver)):
            try:
                await self.change_token_to_selected(driver)
            except RuntimeError:
                data.update(
                    return_signal=ReturnSignal(
                        status_code=StatusCodes.no_tokens_available,
                    )
                )
                return

        if robux < 5 or (int(cost.text.replace(",", "")) > robux and robux < 50):
            try:
                await self.change_token_recursive(driver)
            except RuntimeError:
                data.update(
                    return_signal=ReturnSignal(
                        status_code=StatusCodes.no_tokens_available,
                    )
                )
                return
        press_agreement_button(driver)
        try:
            btn = driver.find_element(By.CLASS_NAME, "PurchaseButton")
            btn.click()
        except NoSuchElementException:
            logger.info("Gamepass has been already bought")
            _temp = ReturnSignal(
                status_code=StatusCodes.already_bought,
            )
            logger.debug("Sending back information about.")
        else:
            confirm_btn = driver.find_element(By.CSS_SELECTOR, "a#confirm-btn.btn-primary-md")
            logger.info("Clicking buy now")
            # HERE IT BUYS GAMEPASS
            if not settings.disabled:
                confirm_btn.click()
            else:
                logger.info("Gamepass buy is disabled!")

            logger.info(f"Purchased gamepass for {cost.text} robuxes")
            _temp = ReturnSignal(
                status_code=StatusCodes.success,
            )
        data.update(return_signal=_temp)


class DataHandler(IListener):
    def setup(self, *args, **kwargs):
        pass

    def close(self, *args, **kwargs):
        pass

    def __call__(self, data: dict, body: bytes, publisher: BasicMessageSender):
        try:
            _temp = json.loads(body)
            pur_data = PurchaseData(**_temp)
        except json.JSONDecodeError:
            logger.error("NOT HELLO")

            raise CancelException
        except pydantic.ValidationError as e:
            logger.info(f"Invalid data: {body}")

            errors = [SendError(name="validation error", info=str(e.errors()))]

            data = ReturnSignal(status_code=StatusCodes.invalid_data, errors=errors)

            publisher.send_message(data.dict())
            raise CancelException

        data.update(purchase_data=pur_data)


class ReturnSignalHandler(IListener):
    def setup(self, *args, **kwargs):
        pass

    def close(self, *args, **kwargs):
        pass

    async def __call__(self, publisher: BasicMessageSender,  purchase_data: PurchaseData, return_signal: ReturnSignal):
        return_signal.tx_id = purchase_data.tx_id
        publisher.send_message(return_signal.dict())
