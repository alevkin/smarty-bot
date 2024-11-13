from sqlmodel import SQLModel, create_engine, Session, select
from src.ton_analyze.models.base import JettonHolder
from dotenv import load_dotenv
import os

# Загружаем переменные окружения из .env файла
load_dotenv()

# Подключение к базе данных
# DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./database.db")
DATABASE_URL = f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}" \
               f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
engine = create_engine(DATABASE_URL)

# Список известных адресов пулов и CEX, DEX
KNOWN_LIQUIDITY_POOLS = {
    "DEX DeDust": "0:6bebcc2448012bba42e151f5d140448cf7be8e22a2233d8da3a1423bdc244aac",
    "DEX StonFi": "0:779dcc815138d9500e449c5291e7f12738c23d575b5310000f6a253bd607384e",
    "CEX xRocket Cold Storage": "0:45614fee399c43d77bb597558791831bc0ee31754cbb2b5b1fbf5a3488ed9940",
    "CEX xRocket Bot": "0:011a8f0a0b36b779af033473274966666d1cd6fb4e77df679375fbd6f970d012",
    "MEXC 3": "0:d887d0e2d1c4fc4126e71c970d33ab1896940000eae703bb1ab6cecc830777e3",
    "Burned": "0:0000000000000000000000000000000000000000000000000000000000000000",
    "Anon Space Staking": "0:e3fa13950c93bab4f9b7901abd7959f8111e8dabc0aae76e6c6000683068241d"
}

# Функция для получения фиксированной цены токена (пока установлена вручную)
def get_token_price_in_usd():
    return 0.000061  # Установленная цена токена в USD

# Функция для создания когорт с учетом пулов ликвидности
def create_cohorts(session):
    cohorts = {
        "Микро-держатели ($0 - $34)": {"holders": 0, "total_balance": 0, "total_value_usd": 0},
        "Малые держатели ($34 - $170)": {"holders": 0, "total_balance": 0, "total_value_usd": 0},
        "Средние держатели ($170 - $3400)": {"holders": 0, "total_balance": 0, "total_value_usd": 0},
        "Крупные держатели ($3400 - $34,000)": {"holders": 0, "total_balance": 0, "total_value_usd": 0},
        "Сверх-крупные держатели ($34,000+)": {"holders": 0, "total_balance": 0, "total_value_usd": 0},
        "Liquidity Pools & CEX": {"holders": 0, "total_balance": 0, "total_value_usd": 0}  # Отдельная когорта для пулов
    }

    token_price_usd = get_token_price_in_usd()

    # Запрос к базе данных, чтобы получить всех холдеров
    statement = select(JettonHolder)
    holders = session.exec(statement).all()

    for holder in holders:
        balance = holder.balance
        value_usd = balance * token_price_usd

        # Проверяем, является ли адрес холдера пулом ликвидности или CEX
        if holder.holder_address in KNOWN_LIQUIDITY_POOLS.values():
            cohort_key = "Liquidity Pools & CEX"
        else:
            # Формируем когорты по стоимости холдингов в USD
            if 0 <= value_usd < 34:
                cohort_key = "Микро-держатели ($0 - $34)"
            elif 34 <= value_usd < 170:
                cohort_key = "Малые держатели ($34 - $170)"
            elif 170 <= value_usd < 3400:
                cohort_key = "Средние держатели ($170 - $3400)"
            elif 3400 <= value_usd < 34000:
                cohort_key = "Крупные держатели ($3400 - $34,000)"
            else:
                cohort_key = "Сверх-крупные держатели ($34,000+)"

        # Обновляем данные для текущей когорты
        cohorts[cohort_key]["holders"] += 1
        cohorts[cohort_key]["total_balance"] += balance
        cohorts[cohort_key]["total_value_usd"] += value_usd

    return cohorts

# Функция для отображения результатов
def display_cohort_data(cohorts):
    print("Cohort Analysis:")
    print(f"{'Cohort':<35} {'Holders':<10} {'Total Balance':<15} {'Total Value (USD)':<15}")
    for cohort, data in cohorts.items():
        print(f"{cohort:<35} {data['holders']:<10} {data['total_balance']:<15,.2f} {data['total_value_usd']:<15,.2f}")

# Основная функция для работы с базой и создания когорт
def main():
    # Создаем сессию для работы с базой данных
    with Session(engine) as session:
        cohorts = create_cohorts(session)
        display_cohort_data(cohorts)

if __name__ == "__main__":
    main()