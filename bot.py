# File name: bot_pharos_combined.py

import time
import random
import requests
import json
import concurrent.futures
from web3 import Web3
from web3.exceptions import TransactionNotFound
from eth_account import Account
from eth_account.messages import encode_defunct
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from datetime import datetime, timedelta
from rich.live import Live
import asyncio
import aiohttp
from aiohttp_socks import ProxyConnector
from fake_useragent import FakeUserAgent
import pytz

wib = pytz.timezone('Asia/Jakarta')
console = Console()

# --- CONFIGURATION SECTION ---
class Config:
    PRIVATE_KEY_FILE = "accounts.txt"  # Changed to match PharosTestnet
    RPC_URL = "https://api.zan.top/node/v1/pharos/testnet/ef2693fcb98646c694885bc318c00126"
    BASE_API = "https://api.pharosnetwork.xyz"
    WPHRS_ADDRESS = "0x3019B247381c850ab53Dc0EE53bCe7A07Ea9155f"
    USDC_ADDRESS = "0x72df0bcd7276f2dFbAc900D1CE63c272C4BCcCED"
    USDT_ADDRESS = "0xD4071393f8716661958F766DF660033b3d35fD29"
    SWAP_ROUTER_ADDRESS = "0x3541423f25a1Ca5C98fdBCf478405d3f0aaD1164"
    POSITION_MANAGER_ADDRESS = "0x4b177aded3b8bd1d5d747f91b9e853513838cd49"
    REF_CODE = "8G8MJ3zGE5B7tJgP"

    BASE_HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://testnet.pharosnetwork.xyz",
        "Referer": "https://testnet.pharosnetwork.xyz/",
        "User-Agent": FakeUserAgent().random
    }

# --- ABI SECTION ---
ERC20_ABI = json.loads('''[
    {"type":"function","name":"balanceOf","stateMutability":"view","inputs":[{"name":"address","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
    {"type":"function","name":"allowance","stateMutability":"view","inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
    {"type":"function","name":"approve","stateMutability":"nonpayable","inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[{"name":"","type":"bool"}]},
    {"type":"function","name":"decimals","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint8"}]},
    {"type":"function","name":"deposit","stateMutability":"payable","inputs":[],"outputs":[]},
    {"type":"function","name":"withdraw","stateMutability":"nonpayable","inputs":[{"name":"wad","type":"uint256"}],"outputs":[]}
]''')

# --- API FUNCTIONS ---
async def user_login(address, signature, proxy=None):
    url = f"{Config.BASE_API}/user/login?address={address}&signature={signature}&invite_code={Config.REF_CODE}"
    headers = {**Config.BASE_HEADERS, "Authorization": "Bearer null", "Content-Length": "0"}
    async with aiohttp.ClientSession(connector=ProxyConnector.from_url(proxy) if proxy else None) as session:
        async with session.post(url, headers=headers) as response:
            response.raise_for_status()
            return await response.json()

async def claim_faucet(address, access_token, proxy=None):
    url = f"{Config.BASE_API}/faucet/daily?address={address}"
    headers = {**Config.BASE_HEADERS, "Authorization": f"Bearer {access_token}", "Content-Length": "0"}
    async with aiohttp.ClientSession(connector=ProxyConnector.from_url(proxy) if proxy else None) as session:
        async with session.post(url, headers=headers) as response:
            response.raise_for_status()
            return await response.json()

# --- ON-CHAIN FUNCTIONS ---
def get_token_balance(w3, token_address, owner_address):
    try:
        token_contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
        balance = token_contract.functions.balanceOf(owner_address).call()
        decimals = token_contract.functions.decimals().call()
        return balance / (10 ** decimals)
    except Exception as e:
        console.print(f"[red]   Failed to get token balance {token_address[:10]}...: {e}[/red]")
        return 0

def wait_for_transaction(w3, tx_hash, account_address, action_name):
    console.print(f"[yellow]      ({account_address[:6]}) Waiting for {action_name} confirmation...[/yellow]")
    try:
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        return tx_receipt
    except TransactionNotFound:
        console.print(f"[bold red]      ({account_address[:6]}) ❌ Transaction {action_name} not found.[/bold red]")
        return None
    except Exception as e:
        console.print(f"[bold red]      ({account_address[:6]}) ❌ Error while waiting for transaction {action_name}: {e}[/bold red]")
        return None

def approve_token(account, w3, token_address, spender_address, amount, decimals):
    console.print(f"[cyan]   ({account.address[:6]}) Approve Step: Granting permission to {spender_address[:10]}... for token {token_address[:10]}...[/cyan]")
    token_address_checksum = Web3.to_checksum_address(token_address)
    spender_address_checksum = Web3.to_checksum_address(spender_address)
    token_contract = w3.eth.contract(address=token_address_checksum, abi=ERC20_ABI)

    try:
        amount_to_wei = int(amount * (10 ** decimals))
        allowance = token_contract.functions.allowance(account.address, spender_address_checksum).call()
        if allowance >= amount_to_wei:
            console.print(f"[green]      ({account.address[:6]}) ✅ Allowance sufficient. Skipping approve.[/green]")
            return True

        tx_params = {'from': account.address, 'gas': 100000, 'nonce': w3.eth.get_transaction_count(account.address), 'gasPrice': w3.eth.gas_price}
        approve_tx = token_contract.functions.approve(spender_address_checksum, amount_to_wei).build_transaction(tx_params)
        signed_tx = account.sign_transaction(approve_tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        console.print(f"[yellow]      ({account.address[:6]}) 📤 Approve transaction sent! Hash: {tx_hash.hex()[:15]}...[/yellow]")
        
        tx_receipt = wait_for_transaction(w3, tx_hash, account.address, "approve")
        if tx_receipt and tx_receipt['status'] == 1:
            console.print(f"[bold green]      ({account.address[:6]}) ✅ Approve Successful![/bold green]")
            return True
        else:
            console.print(f"[bold red]      ({account.address[:6]}) ❌ Approve Failed![/bold red]")
            return False
    except Exception as e:
        console.print(f"[bold red]      ({account.address[:6]}) ❌ Error during approve process: {e}[/bold red]")
        return False

def perform_swap_v3(account, w3, dex_abi, from_token, to_token, amount, ticker_from, ticker_to):
    router_address = Web3.to_checksum_address(Config.SWAP_ROUTER_ADDRESS)
    dex_router_contract = w3.eth.contract(address=router_address, abi=dex_abi)
    token_contract = w3.eth.contract(address=Web3.to_checksum_address(from_token), abi=ERC20_ABI)
    decimals = token_contract.functions.decimals().call()
    amount_in_wei = int(amount * (10 ** decimals))

    params = {
        'tokenIn': Web3.to_checksum_address(from_token),
        'tokenOut': Web3.to_checksum_address(to_token),
        'fee': 500,  # Default fee tier
        'recipient': account.address,
        'deadline': int(time.time()) + 300,
        'amountIn': amount_in_wei,
        'amountOutMinimum': 0,
        'sqrtPriceLimitX96': 0
    }

    try:
        tx_params = {'from': account.address, 'gas': 400000, 'nonce': w3.eth.get_transaction_count(account.address), 'gasPrice': w3.eth.gas_price}
        if from_token == Config.WPHRS_ADDRESS:
            tx_params['value'] = amount_in_wei

        swap_tx = dex_router_contract.functions.exactInputSingle(params).build_transaction(tx_params)
        signed_tx = account.sign_transaction(swap_tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        console.print(f"[yellow]      ({account.address[:6]}) 📤 Swap transaction sent! Hash: {tx_hash.hex()[:15]}...[/yellow]")
        
        tx_receipt = wait_for_transaction(w3, tx_hash, account.address, f"swap {ticker_from} to {ticker_to}")
        if tx_receipt and tx_receipt['status'] == 1:
            console.print(f"[bold green]      ({account.address[:6]}) ✅ Swap {amount} {ticker_from} to {ticker_to} Successful![/bold green]")
            return True
        else:
            console.print(f"[bold red]      ({account.address[:6]}) ❌ Swap Failed![/bold red]")
            return False
    except Exception as e:
        console.print(f"[bold red]      ({account.address[:6]}) ❌ Error during swap process: {e}[/bold red]")
        return False

def perform_add_liquidity(account, w3, add_lp_abi, token0, token1, amount0, amount1, ticker0, ticker1):
    console.print(f"[cyan]   ({account.address[:6]}) Adding Liquidity: {ticker0}/{ticker1}...[/cyan]")
    token0_contract = w3.eth.contract(address=Web3.to_checksum_address(token0), abi=ERC20_ABI)
    token1_contract = w3.eth.contract(address=Web3.to_checksum_address(token1), abi=ERC20_ABI)
    token_contract = w3.eth.contract(address=Web3.to_checksum_address(Config.POSITION_MANAGER_ADDRESS), abi=add_lp_abi)

    token0_decimals = token0_contract.functions.decimals().call()
    token1_decimals = token1_contract.functions.decimals().call()
    amount0_desired = int(amount0 * (10 ** token0_decimals))
    amount1_desired = int(amount1 * (10 ** token1_decimals))

    mint_params = {
        "token0": Web3.to_checksum_address(token0),
        "token1": Web3.to_checksum_address(token1),
        "fee": 500,
        "tickLower": -887270,
        "tickUpper": 887270,
        "amount0Desired": amount0_desired,
        "amount1Desired": amount1_desired,
        "amount0Min": 0,
        "amount1Min": 0,
        "recipient": account.address,
        "deadline": int(time.time()) + 600
    }

    try:
        tx_params = {'from': account.address, 'gas': 600000, 'nonce': w3.eth.get_transaction_count(account.address), 'gasPrice': w3.eth.gas_price}
        lp_tx = token_contract.functions.mint(mint_params).build_transaction(tx_params)
        signed_tx = account.sign_transaction(lp_tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        console.print(f"[yellow]      ({account.address[:6]}) 📤 Liquidity transaction sent! Hash: {tx_hash.hex()[:15]}...[/yellow]")
        
        tx_receipt = wait_for_transaction(w3, tx_hash, account.address, f"add liquidity {ticker0}/{ticker1}")
        if tx_receipt and tx_receipt['status'] == 1:
            console.print(f"[bold green]      ({account.address[:6]}) ✅ Add Liquidity {amount0} {ticker0}/{amount1} {ticker1} Successful![/bold green]")
            return True
        else:
            console.print(f"[bold red]      ({account.address[:6]}) ❌ Add Liquidity Failed![/bold red]")
            return False
    except Exception as e:
        console.print(f"[bold red]      ({account.address[:6]}) ❌ Error during add liquidity process: {e}[/bold red]")
        return False

def perform_wrapped(account, w3, amount):
    console.print(f"[cyan]   ({account.address[:6]}) Wrapping {amount} PHRS to WPHRS...[/cyan]")
    token_contract = w3.eth.contract(address=Web3.to_checksum_address(Config.WPHRS_ADDRESS), abi=ERC20_ABI)
    amount_to_wei = w3.to_wei(amount, 'ether')

    try:
        wrap_data = token_contract.functions.deposit()
        tx_params = {
            'from': account.address,
            'value': amount_to_wei,
            'gas': 200000,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gasPrice': w3.eth.gas_price
        }
        wrap_tx = wrap_data.build_transaction(tx_params)
        signed_tx = account.sign_transaction(wrap_tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        console.print(f"[yellow]      ({account.address[:6]}) 📤 Wrap transaction sent! Hash: {tx_hash.hex()[:15]}...[/yellow]")
        
        tx_receipt = wait_for_transaction(w3, tx_hash, account.address, "wrap")
        if tx_receipt and tx_receipt['status'] == 1:
            console.print(f"[bold green]      ({account.address[:6]}) ✅ Wrap {amount} PHRS to WPHRS Successful![/bold green]")
            return True
        else:
            console.print(f"[bold red]      ({account.address[:6]}) ❌ Wrap Failed![/bold red]")
            return False
    except Exception as e:
        console.print(f"[bold red]      ({account.address[:6]}) ❌ Error during wrap process: {e}[/bold red]")
        return False

def perform_unwrapped(account, w3, amount):
    console.print(f"[cyan]   ({account.address[:6]}) Unwrapping {amount} WPHRS to PHRS...[/cyan]")
    token_contract = w3.eth.contract(address=Web3.to_checksum_address(Config.WPHRS_ADDRESS), abi=ERC20_ABI)
    amount_to_wei = w3.to_wei(amount, 'ether')

    try:
        unwrap_data = token_contract.functions.withdraw(amount_to_wei)
        tx_params = {
            'from': account.address,
            'gas': 200000,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gasPrice': w3.eth.gas_price
        }
        unwrap_tx = unwrap_data.build_transaction(tx_params)
        signed_tx = account.sign_transaction(unwrap_tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        console.print(f"[yellow]      ({account.address[:6]}) 📤 Unwrap transaction sent! Hash: {tx_hash.hex()[:15]}...[/yellow]")
        
        tx_receipt = wait_for_transaction(w3, tx_hash, account.address, "unwrap")
        if tx_receipt and tx_receipt['status'] == 1:
            console.print(f"[bold green]      ({account.address[:6]}) ✅ Unwrap {amount} WPHRS to PHRS Successful![/bold green]")
            return True
        else:
            console.print(f"[bold red]      ({account.address[:6]}) ❌ Unwrap Failed![/bold red]")
            return False
    except Exception as e:
        console.print(f"[bold red]      ({account.address[:6]}) ❌ Error during unwrap process: {e}[/bold red]")
        return False

def generate_swap_option():
    swap_options = [
        ("WPHRStoUSDC", Config.WPHRS_ADDRESS, Config.USDC_ADDRESS, "WPHRS", "USDC", 0.001),
        ("WPHRStoUSDT", Config.WPHRS_ADDRESS, Config.USDT_ADDRESS, "WPHRS", "USDT", 0.001),
        ("USDCtoWPHRS", Config.USDC_ADDRESS, Config.WPHRS_ADDRESS, "USDC", "WPHRS", 0.45),
        ("USDTtoWPHRS", Config.USDT_ADDRESS, Config.WPHRS_ADDRESS, "USDT", "WPHRS", 0.45),
        ("USDCtoUSDT", Config.USDC_ADDRESS, Config.USDT_ADDRESS, "USDC", "USDT", 1),
        ("USDTtoUSDC", Config.USDT_ADDRESS, Config.USDC_ADDRESS, "USDT", "USDC", 1)
    ]
    return random.choice(swap_options)

def generate_add_lp_option():
    lp_options = [
        ("USDCnWPHRS", Config.USDC_ADDRESS, Config.WPHRS_ADDRESS, 0.45, 0.001, "USDC", "WPHRS"),
        ("USDCnUSDT", Config.USDC_ADDRESS, Config.USDT_ADDRESS, 1, 1, "USDC", "USDT"),
        ("WPHRSnUSDT", Config.WPHRS_ADDRESS, Config.USDT_ADDRESS, 0.001, 0.45, "WPHRS", "USDT")
    ]
    return random.choice(lp_options)

# --- USER INPUT ---
def get_user_input():
    console.print("[bold green]Select Option:[/bold green]")
    console.print("[white]1. Wrap/Unwrap PHRS[/white]")
    console.print("[white]2. Add Liquidity Pool[/white]")
    console.print("[white]3. Swap WPHRS/USDC/USDT[/white]")
    console.print("[white]4. Run All Features[/white]")
    while True:
        try:
            option = int(console.input("[bold blue]Choose [1/2/3/4] -> [/bold blue]"))
            if option in [1, 2, 3, 4]:
                break
            console.print("[red]Please enter 1, 2, 3, or 4.[/red]")
        except ValueError:
            console.print("[red]Invalid input. Enter a number.[/red]")

    wrap_option = None
    wrap_amount = 0
    add_lp_count = 0
    swap_count = 0
    amounts = {"WPHRS": 0, "USDC": 0, "USDT": 0}
    min_delay = 0
    max_delay = 0

    if option == 1:
        console.print("[bold green]Select Wrap Option:[/bold green]")
        console.print("[white]1. Wrap PHRS to WPHRS[/white]")
        console.print("[white]2. Unwrap WPHRS to PHRS[/white]")
        while True:
            try:
                wrap_option = int(console.input("[bold blue]Choose [1/2] -> [/bold blue]"))
                if wrap_option in [1, 2]:
                    break
                console.print("[red]Please enter 1 or 2.[/red]")
            except ValueError:
                console.print("[red]Invalid input. Enter a number.[/red]")
        while True:
            try:
                wrap_amount = float(console.input("[bold yellow]Enter Amount (e.g., 1, 0.01, 0.001) -> [/bold yellow]"))
                if wrap_amount > 0:
                    break
                console.print("[red]Amount must be greater than 0.[/red]")
            except ValueError:
                console.print("[red]Invalid input. Enter a number.[/red]")

    if option in [2, 4]:
        while True:
            try:
                add_lp_count = int(console.input("[bold yellow]How Many Times to Add Liquidity? -> [/bold yellow]"))
                if add_lp_count > 0:
                    break
                console.print("[red]Please enter a positive number.[/red]")
            except ValueError:
                console.print("[red]Invalid input. Enter a number.[/red]")

    if option in [3, 4]:
        for token in ["WPHRS", "USDC", "USDT"]:
            while True:
                try:
                    amount = float(console.input(f"[bold yellow]{token} Swap Amount? (e.g., 1, 0.01, 0.001) -> [/bold yellow]"))
                    if amount > 0:
                        amounts[token] = amount
                        break
                    console.print("[red]Amount must be greater than 0.[/red]")
                except ValueError:
                    console.print("[red]Invalid input. Enter a number.[/red]")
        while True:
            try:
                swap_count = int(console.input("[bold yellow]How Many Times to Swap? -> [/bold yellow]"))
                if swap_count > 0:
                    break
                console.print("[red]Please enter a positive number.[/red]")
            except ValueError:
                console.print("[red]Invalid input. Enter a number.[/red]")

    if option in [2, 3, 4]:
        while True:
            try:
                min_delay = int(console.input("[bold yellow]Min Delay Each Tx (seconds) -> [/bold yellow]"))
                if min_delay >= 0:
                    break
                console.print("[red]Min Delay must be >= 0.[/red]")
            except ValueError:
                console.print("[red]Invalid input. Enter a number.[/red]")
        while True:
            try:
                max_delay = int(console.input("[bold yellow]Max Delay Each Tx (seconds) -> [/bold yellow]"))
                if max_delay >= min_delay:
                    break
                console.print("[red]Max Delay must be >= Min Delay.[/red]")
            except ValueError:
                console.print("[red]Invalid input. Enter a number.[/red]")

    console.print("[bold green]Proxy Options:[/bold green]")
    console.print("[white]1. Use Free Proxyscrape Proxy[/white]")
    console.print("[white]2. Use Private Proxy[/white]")
    console.print("[white]3. No Proxy[/white]")
    while True:
        try:
            proxy_choice = int(console.input("[bold blue]Choose [1/2/3] -> [/bold blue]"))
            if proxy_choice in [1, 2, 3]:
                break
            console.print("[red]Please enter 1, 2, or 3.[/red]")
        except ValueError:
            console.print("[red]Invalid input. Enter a number.[/red]")

    rotate_proxy = False
    if proxy_choice in [1, 2]:
        while True:
            rotate = console.input("[bold blue]Rotate Invalid Proxy? [y/n] -> [/bold blue]").strip().lower()
            if rotate in ["y", "n"]:
                rotate_proxy = rotate == "y"
                break
            console.print("[red]Invalid input. Enter 'y' or 'n'.[/red]")

    return option, wrap_option, wrap_amount, add_lp_count, swap_count, amounts, min_delay, max_delay, proxy_choice, rotate_proxy

# --- MAIN PROCESS ---
async def process_account_async(account, index, total, w3, dex_abi, add_lp_abi, option, wrap_option, wrap_amount, add_lp_count, swap_count, amounts, min_delay, max_delay, proxies, proxy_index, rotate_proxy):
    try:
        account_obj = Account.from_key(account)
    except Exception:
        console.print(f"[bold red]❌ Private key [Account {index}] is invalid. Skipping.[/bold red]")
        return

    console.print(Rule(f"[bold]Processing Account {index}/{total} | {account_obj.address}[/bold]"))
    
    # API Login
    signature = Account.sign_message(encode_defunct(text="pharos"), private_key=account).signature.hex()
    proxy = proxies[proxy_index % len(proxies)] if proxies else None
    login = await user_login(account_obj.address, signature, proxy)
    if login and login.get("code") == 0:
        access_token = login["data"]["jwt"]
        console.print(f"[bold green]      ({account_obj.address[:6]}) ✅ Login Successful![/bold green]")
    else:
        console.print(f"[bold red]      ({account_obj.address[:6]}) ❌ Login Failed![/bold red]")
        return

    # Faucet Claim
    faucet = await claim_faucet(account_obj.address, access_token, proxy)
    if faucet and faucet.get("code") in [0, 1]:
        console.print(f"[bold green]      ({account_obj.address[:6]}) ✅ Faucet Claimed![/bold green]")
    else:
        console.print(f"[bold red]      ({account_obj.address[:6]}) ❌ Faucet Claim Failed![/bold red]")

    # Option 1: Wrap/Unwrap
    if option == 1:
        if wrap_option == 1:
            balance = w3.eth.get_balance(account_obj.address) / 10**18
            if balance < wrap_amount:
                console.print(f"[yellow]      ({account_obj.address[:6]}) Insufficient PHRS balance: {balance}[/yellow]")
                return
            perform_wrapped(account_obj, w3, wrap_amount)
        elif wrap_option == 2:
            balance = get_token_balance(w3, Config.WPHRS_ADDRESS, account_obj.address)
            if balance < wrap_amount:
                console.print(f"[yellow]      ({account_obj.address[:6]}) Insufficient WPHRS balance: {balance}[/yellow]")
                return
            perform_unwrapped(account_obj, w3, wrap_amount)

    # Option 2: Add Liquidity
    if option == 2 or option == 4:
        for i in range(add_lp_count):
            console.print(Rule(f"[bold magenta]({account_obj.address[:6]}) 🚀 Liquidity Iteration {i + 1}/{add_lp_count} 🚀[/bold magenta]"))
            lp_option, token0, token1, amount0, amount1, ticker0, ticker1 = generate_add_lp_option()
            
            token0_balance = get_token_balance(w3, token0, account_obj.address)
            token1_balance = get_token_balance(w3, token1, account_obj.address)
            
            console.print(f"[dim]      ({account_obj.address[:6]}) Balances: {token0_balance} {ticker0}, {token1_balance} {ticker1}[/dim]")
            console.print(f"[dim]      ({account_obj.address[:6]}) Amounts: {amount0} {ticker0}, {amount1} {ticker1}[/dim]")
            
            if token0_balance < amount0 or token1_balance < amount1:
                console.print(f"[yellow]      ({account_obj.address[:6]}) Insufficient balance for {ticker0}/{ticker1}[/yellow]")
                continue

            token0_contract = w3.eth.contract(address=Web3.to_checksum_address(token0), abi=ERC20_ABI)
            token1_contract = w3.eth.contract(address=Web3.to_checksum_address(token1), abi=ERC20_ABI)
            token0_decimals = token0_contract.functions.decimals().call()
            token1_decimals = token1_contract.functions.decimals().call()

            if lp_option in ["USDCnWPHRS", "USDCnUSDT"]:
                approve_token(account_obj, w3, token0, Config.POSITION_MANAGER_ADDRESS, amount0, token0_decimals)
                if lp_option == "USDCnUSDT":
                    approve_token(account_obj, w3, token1, Config.POSITION_MANAGER_ADDRESS, amount1, token1_decimals)
            else:
                approve_token(account_obj, w3, token1, Config.POSITION_MANAGER_ADDRESS, amount1, token1_decimals)

            perform_add_liquidity(account_obj, w3, add_lp_abi, token0, token1, amount0, amount1, ticker0, ticker1)
            time.sleep(random.uniform(min_delay, max_delay))

    # Option 3: Swap
    if option == 3 or option == 4:
        for i in range(swap_count):
            console.print(Rule(f"[bold magenta]({account_obj.address[:6]}) 🚀 Swap Iteration {i + 1}/{swap_count} 🚀[/bold magenta]"))
            _, from_token, to_token, from_ticker, to_ticker, _ = generate_swap_option()
            swap_amount = amounts[from_ticker]
            
            balance = get_token_balance(w3, from_token, account_obj.address)
            console.print(f"[dim]      ({account_obj.address[:6]}) Balance: {balance} {from_ticker}[/dim]")
            console.print(f"[dim]      ({account_obj.address[:6]}) Swap Amount: {swap_amount} {from_ticker}[/dim]")
            
            if balance < swap_amount:
                console.print(f"[yellow]      ({account_obj.address[:6]}) Insufficient {from_ticker} balance[/yellow]")
                continue

            token_contract = w3.eth.contract(address=Web3.to_checksum_address(from_token), abi=ERC20_ABI)
            decimals = token_contract.functions.decimals().call()
            approve_token(account_obj, w3, from_token, Config.SWAP_ROUTER_ADDRESS, swap_amount, decimals)
            perform_swap_v3(account_obj, w3, dex_abi, from_token, to_token, swap_amount, from_ticker, to_ticker)
            time.sleep(random.uniform(min_delay, max_delay))

def process_account(private_key, index, total, w3, dex_abi, add_lp_abi, option, wrap_option, wrap_amount, add_lp_count, swap_count, amounts, min_delay, max_delay, proxies, proxy_index, rotate_proxy):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(process_account_async(
            private_key, index, total, w3, dex_abi, add_lp_abi, option, wrap_option, wrap_amount,
            add_lp_count, swap_count, amounts, min_delay, max_delay, proxies, proxy_index, rotate_proxy
        ))
    finally:
        loop.close()

def load_proxies(proxy_choice):
    proxies = []
    if proxy_choice == 1:
        response = requests.get("https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text")
        proxies = [line.strip() for line in response.text.splitlines() if line.strip()]
    elif proxy_choice == 2:
        with open("proxy.txt", 'r') as f:
            proxies = [line.strip() for line in f.read().splitlines() if line.strip()]
    return proxies

def main():
    console.print(Rule("[bold magenta]🚀 Pharos Combined Bot 🚀[/bold magenta]"))
    private_keys = load_private_keys(Config.PRIVATE_KEY_FILE)
    dex_abi = load_json_file('abi.json')
    add_lp_abi = load_json_file('add_lp_abi.json')
    if not private_keys or not dex_abi or not add_lp_abi:
        console.print("[bold red]Bot stopped. Required files missing/invalid.[/bold red]")
        return

    w3 = Web3(Web3.HTTPProvider(Config.RPC_URL))
    if not w3.is_connected():
        console.print(f"[bold red]Failed to connect to RPC Node at {Config.RPC_URL}[/bold red]")
        return
    console.print(f"[green]Connected to RPC Node. Chain ID: {w3.eth.chain_id}[/green]")

    option, wrap_option, wrap_amount, add_lp_count, swap_count, amounts, min_delay, max_delay, proxy_choice, rotate_proxy = get_user_input()
    proxies = load_proxies(proxy_choice) if proxy_choice in [1, 2] else []

    MAX_THREADS = 5
    console.print(f"[green]✅ OK! Running with option {option}, {MAX_THREADS} threads.[/green]")

    run_count = 0
    while True:
        run_count += 1
        console.print(Rule(f"[bold green]🚀 Starting Global Cycle {run_count} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 🚀[/bold green]"))
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            future_to_account = {
                executor.submit(process_account, pk, i + 1, len(private_keys), w3, dex_abi, add_lp_abi, option, wrap_option, wrap_amount, add_lp_count, swap_count, amounts, min_delay, max_delay, proxies, i % len(proxies) if proxies else 0, rotate_proxy): f"Account {i+1}"
                for i, pk in enumerate(private_keys)
            }

            for future in concurrent.futures.as_completed(future_to_account):
                account_info = future_to_account[future]
                try:
                    future.result()
                except Exception as exc:
                    console.print(f"[bold red]❌ {account_info} generated an unexpected error: {exc}[/bold red]")
        
        console.print(Rule("[bold green]✅ All Accounts Processed for This Cycle[/bold green]"))
        run_countdown(24 * 60 * 60)

def load_private_keys(file_path):
    try:
        with open(file_path, 'r') as f:
            keys = [line.strip() for line in f if line.strip()]
            if not keys:
                console.print(f"[bold red]❌ ERROR: File '[italic yellow]{file_path}[/italic yellow]' is empty.[/bold red]")
                return None
            console.print(f"[bold green]✅ Successfully loaded {len(keys)} private keys.[/bold green]")
            return keys
    except FileNotFoundError:
        console.print(f"[bold red]❌ ERROR: File '[italic yellow]{file_path}[/italic yellow]' not found.[/bold red]")
        return None

def load_json_file(file_path):
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        console.print(f"[bold red]❌ ERROR: File '[italic yellow]{file_path}[/italic yellow]' not found.[/bold red]")
        return None
    except json.JSONDecodeError:
        console.print(f"[bold red]❌ ERROR: File '[italic yellow]{file_path}[/italic yellow]' is invalid.[/bold red]")
        return None

def run_countdown(duration_seconds):
    end_time = datetime.now() + timedelta(seconds=duration_seconds)
    with Live(console=console, refresh_per_second=1) as live:
        while datetime.now() < end_time:
            remaining = end_time - datetime.now()
            total_seconds = int(remaining.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            live.update(Panel(f"Next cycle: [bold cyan]{hours:02}:{minutes:02}:{seconds:02}[/bold cyan]", title="[bold green]💤 Delay Time[/bold green]"))
            time.sleep(1)

if __name__ == "__main__":
    main()
