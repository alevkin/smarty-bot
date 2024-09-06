from pytonapi import AsyncTonapi
# dotenv is used to load the API key from the .env file
from dotenv import load_dotenv
import os
import sqlite3
import base64

# Load the API key from the .env file
load_dotenv()

JETTON_DECIMALS = 9

# Create a connection to the SQLite database (or modify to use another database)
db_conn = sqlite3.connect('jetton_holders.db')
cursor = db_conn.cursor()

# Create the table if it doesn't exist
cursor.execute('''
    CREATE TABLE IF NOT EXISTS jetton_holders (
        owner_address_raw TEXT,
        owner_name TEXT,
        balance REAL
    )
''')

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
    def read_friendly_address(cls, address):
        urlsafe = False
        if set(address).issubset(cls.b64_abc):
            address_bytes = base64.b64decode(address.encode("utf8"))
        elif set(address).issubset(cls.b64_abc_urlsafe):
            urlsafe = True
            address_bytes = base64.urlsafe_b64decode(address.encode("utf8"))
        else:
            raise Exception("Not an address")
        if not cls.calcCRC(address_bytes[:-2]) == address_bytes[-2:]:
            raise Exception("Wrong checksum")
        tag = address_bytes[0]
        if tag & 0x80:
            test_only = True
            tag = tag ^ 0x80
        else:
            test_only = False
        tag = tag.to_bytes(1, "big")
        if tag == cls.bounceable_tag:
            bounceable = True
        elif tag == cls.non_bounceable_tag:
            bounceable = False
        else:
            raise Exception("Unknown tag")
        if address_bytes[1:2] == b"\xff":
            workchain = -1
        else:
            workchain = address_bytes[1]
        hx = hex(int.from_bytes(address_bytes[2:-2], "big"))[2:]
        hx = (64 - len(hx)) * "0" + hx
        raw_form = str(workchain) + ":" + hx
        account = cls.account_forms(raw_form, test_only)
        account["given_type"] = "friendly_" + (
            "bounceable" if bounceable else "non_bounceable"
        )
        return account

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

async def process_jetton_holders(tonapi, jetton_holders, jetton_decimals):
    for holder in jetton_holders.addresses:
        owner_address_raw = holder.owner.address.root
        owner_address_nonbounceable = converter.detect_address(holder.owner.address.root)["non_bounceable"]["b64url"]
        owner_address_bounceable = converter.detect_address(holder.owner.address.root)["bounceable"]["b64url"]
        owner_name = holder.owner.name if holder.owner.name else "Unknown"
        raw_balance = int(holder.balance)

        # Adjust balance according to jetton decimals
        balance = raw_balance / (10 ** jetton_decimals)

        # Insert holder data into the database
        cursor.execute('''
            INSERT INTO jetton_holders (owner_address_raw, owner_name, balance)
            VALUES (?, ?, ?)
        ''', (owner_address_raw, owner_name, balance))

    # Commit the transaction
    db_conn.commit()


# Declare an asynchronous function for using await
async def main():
    # Create a new Tonapi object with the provided API key
    tonapi = AsyncTonapi(api_key=os.getenv("TON_API_KEY"))

    # Specify the account ID
    account_id = os.getenv("TON_WALLET_ADDRESS")  # noqa
    jettton_master_address = os.getenv("TON_JETTON_ADDRESS")  # noqa

    # Retrieve account information asynchronously
    account = await tonapi.accounts.get_info(account_id=account_id)
    #jetton = await tonapi.jettons.get_info(account_id=jettton_master_address)
    #jetton_holders = await tonapi.jettons.get_holders(account_id=jettton_master_address)
    # Print account details
    #print(f"Account Address (raw): {account.address.to_raw()}")
    #print(f"Account Balance (nanoton): {account.balance.to_nano()}")
    print(f"Account methods: {account.get_methods}")
    print(f"Account interfaces: {account.interfaces}")

    if account.is_wallet:
        print(f"Account name: {account.name}")
        print(f"Account Address (userfriendly): {account.address.to_userfriendly(is_bounceable=True)}")
        print(f"It is a wallet")
        print(f"Account Wallet balance: {account.balance.to_amount()} TON")
    else:
        jetton = await tonapi.jettons.get_info(account_id=jettton_master_address)
        jetton_holders = await tonapi.jettons.get_holders(account_id=jettton_master_address)
        print(f"Jetton name: {jetton.metadata.name}")
        print(f"Jetton symbol: {jetton.metadata.symbol}")
        print(f"Jetton description: {jetton.metadata.description}")
        # Get the total supply of the jetton from decimals and total supply in raw
        jetton_total_supply = int(jetton.total_supply) / (10 ** int(jetton.metadata.decimals))
        print(f"Jetton total supply: {int(jetton_total_supply)} {jetton.metadata.symbol}")
        print(f"Jetton icon: {jetton.metadata.image}")
        print(f"Jetton Holders count: {jetton.holders_count}")
        # Process and store holders in the database
        await process_jetton_holders(tonapi, jetton_holders, int(jetton.metadata.decimals))


    #print(f"Jetton Holders count: {jetton.holders_count}")
    #print(f"Jetton Holders: {jetton_holders}")



if __name__ == '__main__':
    import asyncio

    # Run the asynchronous function
    asyncio.run(main())
