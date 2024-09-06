from sqlmodel import SQLModel, Field, Relationship
from typing import List, Optional
from datetime import datetime


# Модель для Jetton (Жетон)
class Jetton(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    jetton_name: str
    jetton_symbol: str
    jetton_decimals: int
    total_supply: Optional[float]
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Связь с таблицей jetton_holders (один ко многим)
    holders: List["JettonHolder"] = Relationship(back_populates="jetton")


# Модель для JettonHolder (Держатель жетонов)
class JettonHolder(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    holder_address: str  # Адрес холдера (владеет жетоном)
    owner_name: Optional[str] = None  # Имя владельца (если известно)
    balance: float = 0.0  # Баланс жетонов

    jetton_id: Optional[int] = Field(default=None, foreign_key="jetton.id")
    
    jetton: Optional[Jetton] = Relationship(back_populates="holders")  # Связь с Jetton


# Модель для Snapshot (снимок балансов)
class Snapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    snapshot_date: datetime = Field(default_factory=datetime.utcnow)
    balance: float = 0.0  # Баланс жетонов в момент снимка

    jetton_holder_id: Optional[int] = Field(default=None, foreign_key="jettonholder.id")
    jetton_holder: Optional[JettonHolder] = Relationship()  # Связь с держателем