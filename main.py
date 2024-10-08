# main.py

import sys
import logging
import asyncio
import time
from token import AWAIT

import pytonconnect.exceptions
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from pytoniq_core import Address
from pytonconnect import TonConnect

import config
from messages import get_comment_message
from connector import get_connector

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from io import BytesIO
import qrcode
from aiogram.types import BufferedInputFile
from database import add_user_wallet, user_wallet_exists, init_db, get_next_id

# Инициализация базы данных и ключей
init_db()
key_data = get_next_id()

logger = logging.getLogger(__file__)

dp = Dispatcher()
bot = Bot(
    token=config.TOKEN,
    session=AiohttpSession(),  # Необходимо передавать сессию, начиная с aiogram 3.7.0
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

@dp.message(CommandStart())
async def command_start_handler(message: Message):
    chat_id = message.chat.id
    connector = get_connector(chat_id)
    connected = await connector.restore_connection()

    mk_b = InlineKeyboardBuilder()
    if connected:
        mk_b.button(text='Получить токены', callback_data='send_tr')
        mk_b.button(text='Отключится', callback_data='disconnect')
        await message.answer(text='Вы успешно подключены', reply_markup=mk_b.as_markup())

    else:
        wallets_list = TonConnect.get_wallets()
        for wallet in wallets_list:
            mk_b.button(text=wallet['name'], callback_data=f'connect:{wallet["name"]}')
        mk_b.adjust(1, )
        await message.answer(text='Выберете предпочитаемый кошелек', reply_markup=mk_b.as_markup())

user_try_claim_key = {}
@dp.message(Command('transaction'))
async def send_transaction(message: Message):
    connector = get_connector(message.chat.id)
    connected = await connector.restore_connection()
    global key_data, user_try_claim_key
    if not connected:
        await message.answer('Сначала подключите кошелек!')
        return

    user_id = str(message.chat.id)

    if user_id not in user_try_claim_key:
        user_try_claim_key[user_id] = key_data
        key_data += 1


    transaction = {
        'valid_until': int(time.time() + 3600),
        'messages': [
            get_comment_message(user_try_claim_key[user_id])
        ]
    }

    wallet_address = connector.account.address
    # Проверка на наличие ID пользователя или адреса кошелька в базе данных
    if user_wallet_exists(user_id=user_id , wallet_address=wallet_address):
        await message.answer('Лимит ваших наград исчерпан :(')
        return

    await message.answer(text=f'Вы получаете награду с ключом: {user_try_claim_key[user_id]}')
    await message.answer(text='Подтвердите сообщение в своем кошельке!')
    try:
        await asyncio.wait_for(connector.send_transaction(transaction=transaction), 300)

        # Запись в базу данных
        if add_user_wallet(user_id, wallet_address):
            await message.answer(text='Поздравляем! Вы получили свою награду!\nЛимит ваших наград исчерпан')
        else:
            await message.answer('Ошибка: пользователь или кошелек уже существует в базе данных.')

    except asyncio.TimeoutError:
        await message.answer(text='Кошелек не был подключен')
    except pytonconnect.exceptions.UserRejectsError:
        await message.answer(text='Вы отменили транзакцию!')
    except Exception as e:
        await message.answer(text=f'Ошибка: {e}')



async def connect_wallet(message: Message, wallet_name: str):
    connector = get_connector(message.chat.id)

    wallets_list = connector.get_wallets()
    wallet = None

    for w in wallets_list:
        if w['name'] == wallet_name:
            wallet = w

    if wallet is None:
        raise Exception(f'Неизвестный кошелек: {wallet_name}')

    generated_url = await connector.connect(wallet)

    mk_b = InlineKeyboardBuilder()
    mk_b.button(text='Connect', url=generated_url)

    img = qrcode.make(generated_url)
    stream = BytesIO()
    img.save(stream)
    file = BufferedInputFile(file=stream.getvalue(), filename='qrcode')

    await message.answer_photo(photo=file, caption='Подключите кошелек в течении 3 минут', reply_markup=mk_b.as_markup())


    mk_b = InlineKeyboardBuilder()
    mk_b.button(text='Start', callback_data='start')

    for i in range(1, 180):
        await asyncio.sleep(1)
        if connector.connected:
            if connector.account.address:
                wallet_address = connector.account.address
                wallet_address = Address(wallet_address).to_str(is_bounceable=False)
                await message.answer(f'Вы подключены с кошелька: <code>{wallet_address}</code>', reply_markup=mk_b.as_markup())
                logger.info(f'Connected with address: {wallet_address}')
            return

    await message.answer(f'Timeout error!', reply_markup=mk_b.as_markup())


async def disconnect_wallet(message: Message):
    connector = get_connector(message.chat.id)
    await connector.restore_connection()
    await connector.disconnect()
    await message.answer('Вы отключены!')


@dp.callback_query(lambda call: True)
async def main_callback_handler(call: CallbackQuery):
    await call.answer()
    message = call.message
    data = call.data
    if data == "start":
        await command_start_handler(message)
    elif data == "send_tr":
        await send_transaction(message)
    elif data == 'disconnect':
        await disconnect_wallet(message)
    else:
        data = data.split(':')
        if data[0] == 'connect':
            await connect_wallet(message, data[1])


async def main() -> None:
    await bot.delete_webhook(drop_pending_updates=True)  # skip_updates = True
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
