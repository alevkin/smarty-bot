from pytonapi import AsyncTonapi
from dotenv import load_dotenv
import os
import base64
from sqlmodel import SQLModel, create_engine, Session, select
from src.ton_analyze.models.base import JettonHolder, Jetton, Snapshot
import asyncio
from datetime import datetime, timezone

from rich import print
from rich.console import Console
from rich.progress import Progress
import time

console = Console()

# Load the API key from the .env file
load_dotenv()

# Читаем переменную TON_API_RATELIMIT из .env
TON_API_RATELIMIT = int(os.getenv("TON_API_RATELIMIT", 1))  # по умолчанию 1 запрос в секунду

# Устанавливаем максимальный лимит в зависимости от API тарифа
API_LIMIT = int(os.getenv("API_LIMIT", 1000))  # Лимит записей на один запрос, по умолчанию 1000

JETTON_DECIMALS = 9

# Создаем подключение к базе данных через SQLModel
# DATABASE_URL = "sqlite:///./database.db"
# DATABASE_URL = "postgresql+psycopg2://smartybot:gfhjkm@localhost/smartybase"
DATABASE_URL = f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}" \
               f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
#engine = create_engine(DATABASE_URL)
engine = create_engine(DATABASE_URL) # Устанавливаем уровень изоляции транзакций

# Создаем таблицы в базе данных (если они еще не созданы)
SQLModel.metadata.create_all(engine)

class TONAddressConverter:
    bounceable_tag = b"\x11"
    non_bounceable_tag = b"\x51"
    b64_abc = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890+/")
    b64_abc_urlsafe = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890_-"
    )

    @staticmethod
    def is_int(x):
        try:
            int(x)
            return True
        except:
            return False

    @staticmethod
    def is_hex(x):
        try:
            int(x, 16)
            return True
        except:
            return False

    @staticmethod
    def calcCRC(message):
        poly = 0x1021
        reg = 0
        message += b"\x00\x00"
        for byte in message:
            mask = 0x80
            while mask > 0:
                reg <<= 1
                if byte & mask:
                    reg += 1
                mask >>= 1
                if reg > 0xFFFF:
                    reg &= 0xFFFF
                    reg ^= poly
        return reg.to_bytes(2, "big")

    @classmethod
    def account_forms(cls, raw_form, test_only=False):
        workchain, address = raw_form.split(":")
        workchain, address = int(workchain), int(address, 16)
        address = address.to_bytes(32, "big")
        workchain_tag = b"\xff" if workchain == -1 else workchain.to_bytes(1, "big")
        btag = cls.bounceable_tag
        nbtag = cls.non_bounceable_tag
        preaddr_b = btag + workchain_tag + address
        preaddr_u = nbtag + workchain_tag + address
        b64_b = base64.b64encode(preaddr_b + cls.calcCRC(preaddr_b)).decode("utf8")
        b64_u = base64.b64encode(preaddr_u + cls.calcCRC(preaddr_u)).decode("utf8")
        b64_b_us = base64.urlsafe_b64encode(preaddr_b + cls.calcCRC(preaddr_b)).decode(
            "utf8"
        )
        b64_u_us = base64.urlsafe_b64encode(preaddr_u + cls.calcCRC(preaddr_u)).decode(
            "utf8"
        )
        return {
            "raw_form": raw_form,
            "bounceable": {"b64": b64_b, "b64url": b64_b_us},
            "non_bounceable": {"b64": b64_u, "b64url": b64_u_us},
            "given_type": "raw_form",
            "test_only": test_only,
        }

    @classmethod
    def detect_address(cls, unknown_form):
        if cls.is_hex(unknown_form):
            return cls.account_forms("-1:" + unknown_form)
        elif (
            (":" in unknown_form)
            and cls.is_int(unknown_form.split(":")[0])
            and cls.is_hex(unknown_form.split(":")[1])
        ):
            return cls.account_forms(unknown_form)
        else:
            return cls.read_friendly_address(unknown_form)

converter = TONAddressConverter()

# Create asynchronous function to get account information
# This function will be called by the main function
# The main function will be called by the if __name__ == '__main__' block

async def get_account_info(address, tonapi):
    # Create a new Tonapi object with the provided API
    account = await tonapi.accounts.get_info(account_id=address)
    return account

async def process_jetton_holders(jetton_holders, jetton_decimals, jetton_info):
    with Session(engine) as session:
        # Проверяем, существует ли уже запись о жетоне
        statement = select(Jetton).where(Jetton.jetton_symbol == jetton_info.metadata.symbol)
        existing_jetton = session.exec(statement).first()

        if not existing_jetton:
            # Если жетон не найден, создаем новую запись в таблице Jetton
            new_jetton = Jetton(
                jetton_name=jetton_info.metadata.name,
                jetton_symbol=jetton_info.metadata.symbol,
                jetton_decimals=jetton_info.metadata.decimals,
                total_supply=int(jetton_info.total_supply) / (10 ** int(jetton_info.metadata.decimals))
            )
            session.add(new_jetton)
            session.commit()  # Сохраняем изменения
            session.refresh(new_jetton)  # Обновляем объект, чтобы получить его id
        else:
            new_jetton = existing_jetton  # Используем уже существующий жетон

        # Таймер для измерения времени вставки
        start_time = time.time()
        total_records = len(jetton_holders)

        # Прогрессбар для отображения процесса вставки
        with Progress() as progress:
            task = progress.add_task("[green]Inserting into database...", total=total_records)

            # Собираем объекты JettonHolder и Snapshot для пакетной вставки
            jetton_holder_objects = []
            snapshot_objects = []

            for holder in jetton_holders:
                owner_address_raw = holder.owner.address.root
                owner_name = holder.owner.name if holder.owner.name else "Unknown"
                raw_balance = int(holder.balance)
                balance = raw_balance / (10 ** jetton_decimals)

                # Проверяем, существует ли уже холдер
                holder_statement = select(JettonHolder).where(JettonHolder.holder_address == owner_address_raw)
                existing_holder = session.exec(holder_statement).first()

                if not existing_holder:
                    new_holder = JettonHolder(
                        holder_address=owner_address_raw,
                        owner_name=owner_name,
                        balance=balance,
                        jetton_id=new_jetton.id
                    )
                    jetton_holder_objects.append(new_holder)
                    holder_id = new_holder.id  # Используем ID для Snapshot
                else:
                    existing_holder.balance = balance
                    existing_holder.jetton_id = new_jetton.id
                    jetton_holder_objects.append(existing_holder)
                    holder_id = existing_holder.id  # Используем ID для Snapshot

                snapshot = Snapshot(
                    jetton_holder_id=holder_id,
                    balance=balance,
                    snapshot_date=datetime.now(timezone.utc)
                )
                snapshot_objects.append(snapshot)
                
                # Обновляем прогресс
                progress.update(task, advance=1)

            # Пакетная вставка холдеров и снимков
            session.bulk_save_objects(jetton_holder_objects)
            session.bulk_save_objects(snapshot_objects)

        # Коммитим все изменения
        session.commit()

        # Расчет скорости
        end_time = time.time()
        elapsed_time = end_time - start_time
        speed = total_records / elapsed_time if elapsed_time > 0 else 0

        # Вывод скорости вставки данных
        console.log(f"[bold yellow]Inserted {total_records} records in {elapsed_time:.2f} seconds "
                    f"({speed:.2f} records/second)[/bold yellow]")

async def fetch_jetton_holders(tonapi, jettton_master_address, offset, semaphore):
    async with semaphore:
        try:
            # Выполняем запрос с ограничением на количество параллельных запросов
            return await tonapi.jettons.get_holders(account_id=jettton_master_address, limit=API_LIMIT, offset=offset)
        except Exception as e:
            console.log(f"[red]Failed to fetch data at offset {offset}: {e}[/red]")
            return None

async def get_all_jetton_holders(tonapi, jettton_master_address):
    all_holders = []
    offset = 0
    semaphore = asyncio.Semaphore(10)  # Устанавливаем ограничение на 10 параллельных запросов

    tasks = []
    while True:
        # Добавляем задачу для каждого запроса
        tasks.append(fetch_jetton_holders(tonapi, jettton_master_address, offset, semaphore))
        offset += API_LIMIT

        # Проверяем, если задачи заполнились и выполняем их
        if len(tasks) >= 10:
            results = await asyncio.gather(*tasks)
            for result in results:
                if result and result.addresses:
                    all_holders.extend(result.addresses)
                else:
                    return all_holders  # Прерываем при отсутствии данных
            tasks.clear()

    return all_holders

# Declare an asynchronous function for using await
async def main():
    # Create a new Tonapi object with the provided API key
    tonapi = AsyncTonapi(api_key=os.getenv("TON_API_KEY"))

    # Specify the account ID
    account_id = os.getenv("TON_WALLET_ADDRESS")
    jettton_master_address = os.getenv("TON_JETTON_ADDRESS")

    # Retrieve account information asynchronously
    account = await tonapi.accounts.get_info(account_id=account_id)

    if account.is_wallet:
        print(f"Account Address (userfriendly): {account.address.to_userfriendly(is_bounceable=True)}")
        print(f"Account Wallet balance: {account.balance.to_amount()} TON")
    else:
        jetton = await tonapi.jettons.get_info(account_id=jettton_master_address)
        all_holders = await get_all_jetton_holders(tonapi, jettton_master_address)
        #jetton_holders = await tonapi.jettons.get_holders(account_id=jettton_master_address)
        print(f"Jetton name: {jetton.metadata.name}")
        print(f"Jetton symbol: {jetton.metadata.symbol}")
        print(f"Jetton total supply: {int(jetton.total_supply) / (10 ** int(jetton.metadata.decimals))} {jetton.metadata.symbol}")
        
        # Process and store holders in the database
        await process_jetton_holders(all_holders, int(jetton.metadata.decimals), jetton)


if __name__ == '__main__':
    asyncio.run(main())