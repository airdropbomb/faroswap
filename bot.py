# File name: bot_faroswap_multithread.py

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

# Standard ABI for ERC20 tokens (for checking balance and approving)
ERC20_ABI = json.loads('[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"payable":false,"stateMutability":"view","type":"function"},{"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"success","type":"bool"}],"payable":false,"stateMutability":"nonpayable","type":"function"},{"constant":true,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"remaining","type":"uint256"}],"payable":false,"stateMutability":"view","type":"function"}]')

# --- CONFIGURATION SECTION ---
class Config:
    PRIVATE_KEY_FILE = "privatekey.txt"
    RPC_URL = "https://testnet.dplabs-internal.com/"

    class FaroSwap:
        ENABLED = True
        ROUTER_ADDRESS = "0x3541423f25A1Ca5C98fdBCf478405d3f0aaD1164"
        USDT_ADDRESS = "0xD4071393f8716661958F766DF660033b3d35fD29"
        WPHRS_ADDRESS = "0x76aaada469d23216be5f7c596fa25f282ff9b364"
        AMOUNT_TO_SWAP = 0.005
        DELAY_AFTER_FAROSWAP = (10, 20)

    class Timers:
        DELAY_BETWEEN_SWAPS = (10, 25)
        DELAY_BETWEEN_ITERATIONS = (45, 90)
        DELAY_FOR_NEXT_RUN = 24 * 60 * 60

    BASE_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        "Referer": "https://testnet.pharosnetwork.xyz/"
    }
# --- END CONFIGURATION SECTION ---

console = Console()

def generate_random_amount(base_amount):
    random_addition = random.randint(1000, 9999) * 1e-6
    final_amount = base_amount + random_addition
    return final_amount

def load_json_file(file_path):
    try:
        with open(file_path, 'r') as f: return json.load(f)
    except FileNotFoundError:
        console.print(f"[bold red]❌ ERROR:[/bold red] File '[italic yellow]{file_path}[/italic yellow]' not found.")
        return None
    except json.JSONDecodeError:
        console.print(f"[bold red]❌ ERROR:[/bold red] File '[italic yellow]{file_path}[/italic yellow]' is invalid.")
        return None

def get_token_balance(w3, token_address, owner_address):
    try:
        token_contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
        balance = token_contract.functions.balanceOf(owner_address).call()
        return balance
    except Exception as e:
        console.print(f"[red]   Failed to get token balance {token_address[:10]}...: {e}[/red]")
        return 0

def wait_for_transaction(w3, tx_hash, account_address, action_name):
    """Function to wait for transaction confirmation with simple print, not live status."""
    console.print(f"[yellow]      ({account_address[:6]}) Waiting for {action_name} confirmation...[/yellow]")
    try:
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        return tx_receipt
    except TransactionNotFound:
        console.print(f"[bold red]      ({account_address[:6]}) ❌ Transaction {action_name} not found (possibly canceled or never reached).[/bold red]")
        return None
    except Exception as e:
        console.print(f"[bold red]      ({account_address[:6]}) ❌ Error while waiting for transaction {action_name}: {e}[/bold red]")
        return None

def approve_token(account, w3, token_address, spender_address, amount_to_approve):
    console.print(f"[cyan]   ({account.address[:6]}) Approve Step: Granting permission to {spender_address[:10]}... for token {token_address[:10]}...[/cyan]")
    token_address_checksum = Web3.to_checksum_address(token_address)
    spender_address_checksum = Web3.to_checksum_address(spender_address)
    token_contract = w3.eth.contract(address=token_address_checksum, abi=ERC20_ABI)

    try:
        allowance = token_contract.functions.allowance(account.address, spender_address_checksum).call()
        if allowance >= amount_to_approve:
            console.print(f"[green]      ({account.address[:6]}) ✅ Allowance sufficient. Skipping approve.[/green]")
            return True

        tx_params = {'from': account.address, 'gas': 100000, 'nonce': w3.eth.get_transaction_count(account.address), 'gasPrice': w3.eth.gas_price}
        approve_tx = token_contract.functions.approve(spender_address_checksum, amount_to_approve).build_transaction(tx_params)
        signed_tx = account.sign_transaction(approve_tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        console.print(f"[yellow]ഗ0      ({account.address[:6]}) 📤 Approve transaction sent! Hash: {tx_hash.hex()[:15]}...[/yellow]")
        
        tx_receipt = wait_for_transaction(w3, tx_hash, account.address, "approve")

        if tx_receipt and tx_receipt['status'] == 1:
            console.print(f"[bold green]      ({account.address[:6]}) ✅ Approve Successful![/bold green]")
            return True
        else:
            console.print(f"[bold red]      ({account.address[:6]}) ❌ Approve Failed! Transaction Reverted or Error.[/bold red]")
            return False
    except Exception as e:
        console.print(f"[bold red]      ({account.address[:6]}) ❌ Error during approve process: {e}[/bold red]")
        return False

def perform_swap_v3(account, w3, dex_abi, router_address_str, token_in, token_out, amount_in_wei, is_from_native):
    router_address = Web3.to_checksum_address(router_address_str)
    dex_router_contract = w3.eth.contract(address=router_address, abi=dex_abi)

    params = {
        'tokenIn': Web3.to_checksum_address(token_in),
        'tokenOut': Web3.to_checksum_address(token_out),
        'fee': 3000,  # Default fee tier for FaroSwap
        'recipient': account.address,
        'deadline': int(time.time()) + 60 * 20,  # 20 minutes deadline
        'amountIn': amount_in_wei,
        'amountOutMinimum': 0,
        'sqrtPriceLimitX96': 0
    }

    try:
        tx_params = {'from': account.address, 'gas': 400000, 'nonce': w3.eth.get_transaction_count(account.address), 'gasPrice': w3.eth.gas_price}
        if is_from_native:
            tx_params['value'] = amount_in_wei

        swap_tx = dex_router_contract.functions.exactInputSingle(params).build_transaction(tx_params)
        signed_tx = account.sign_transaction(swap_tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        console.print(f"[yellow]      ({account.address[:6]}) 📤 Swap transaction sent! Hash: {tx_hash.hex()[:15]}...[/yellow]")
        
        tx_receipt = wait_for_transaction(w3, tx_hash, account.address, "swap")

        if tx_receipt and tx_receipt['status'] == 1:
            console.print(f"[bold green]      ({account.address[:6]}) ✅ Swap Successful![/bold green]")
            return True
        else:
            console.print(f"[bold red]      ({account.address[:6]}) ❌ Swap Failed! Transaction Reverted or Error.[/bold red]")
            return False
    except Exception as e:
        console.print(f"[bold red]      ({account.address[:6]}) ❌ Error during swap process: {e}[/bold red]")
        return False

def process_account(private_key, index, total, w3, dex_abi, loop_count):
    """Function to process each account in a thread."""
    try:
        account = Account.from_key(private_key)
    except Exception:
        console.print(f"[bold red]❌ Private key [Account {index}] is invalid. Skipping.[/bold red]")
        return
    
    console.print(Rule(f"[bold]Processing Account {index}/{total} | {account.address}[/bold]"))

    for i in range(loop_count):
        console.print(Rule(f"[bold magenta]({account.address[:6]}) 🚀 Swap Iteration {i + 1}/{loop_count} 🚀[/bold magenta]"))
        
        if Config.FaroSwap.ENABLED:
            console.print(Rule(f"[bold blue]({account.address[:6]}) FaroSwap: PHRS -> USDT[/bold blue]", style="blue"))
            random_amount_faro = generate_random_amount(Config.FaroSwap.AMOUNT_TO_SWAP)
            amount_to_swap_wei_faro = w3.to_wei(random_amount_faro, 'ether')
            console.print(f"[dim]({account.address[:6]}) Random swap amount FaroSwap: {random_amount_faro:.8f} PHRS[/dim]")
            perform_swap_v3(account, w3, dex_abi, Config.FaroSwap.ROUTER_ADDRESS, Config.FaroSwap.WPHRS_ADDRESS, Config.FaroSwap.USDT_ADDRESS, amount_to_swap_wei_faro, True)
            time.sleep(random.uniform(*Config.FaroSwap.DELAY_AFTER_FAROSWAP))
        
        if i < loop_count - 1:
            delay = random.uniform(*Config.Timers.DELAY_BETWEEN_ITERATIONS)
            console.print(f"[dim]({account.address[:6]}) Iteration delay for {delay:.1f} seconds...[/dim]")
            time.sleep(delay)

def main():
    console.print(Rule("[bold magenta]🚀 FaroSwap Multi-Thread Bot 🚀[/bold magenta]"))
    private_keys = load_private_keys(Config.PRIVATE_KEY_FILE)
    dex_abi = load_json_file('abi.json')
    if not private_keys or not dex_abi:
        console.print("[bold red]Bot stopped. Required files missing/invalid.[/bold red]")
        return
        
    w3 = Web3(Web3.HTTPProvider(Config.RPC_URL))
    if not w3.is_connected():
        console.print(f"[bold red]Failed to connect to RPC Node at {Config.RPC_URL}[/bold red]")
        return
    console.print(f"[green]Connected to RPC Node. Chain ID: {w3.eth.chain_id}[/green]")

    while True:
        try:
            loop_count_str = console.input("[bold yellow]❓ Enter the number of swap loops per account: [/bold yellow]")
            loop_count = int(loop_count_str)
            if loop_count > 0: break
            else: console.print("[red]Number of loops must be greater than 0.[/red]")
        except ValueError:
            console.print("[red]Invalid input. Please enter a number.[/red]")
    
    MAX_THREADS = 5  # Recommended 3-5 to avoid RPC errors
    console.print(f"[green]✅ OK! Each account will perform {loop_count} swap iterations.[/green]")
    console.print(f"[bold blue]🚀 Bot will run with {MAX_THREADS} parallel threads.[/bold blue]")

    run_count = 0
    while True:
        run_count += 1
        console.print(Rule(f"[bold green]🚀 Starting Global Cycle {run_count} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 🚀[/bold green]"))
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            future_to_account = {
                executor.submit(process_account, pk, i + 1, len(private_keys), w3, dex_abi, loop_count): f"Account {i+1}"
                for i, pk in enumerate(private_keys)
            }

            for future in concurrent.futures.as_completed(future_to_account):
                account_info = future_to_account[future]
                try:
                    future.result()
                except Exception as exc:
                    console.print(f"[bold red]❌ {account_info} generated an unexpected error: {exc}[/bold red]")
        
        console.print(Rule("[bold green]✅ All Accounts Processed for This Cycle[/bold green]"))
        run_countdown(Config.Timers.DELAY_FOR_NEXT_RUN)

def load_private_keys(file_path):
    try:
        with open(file_path, 'r') as f:
            keys = [line.strip() for line in f if line.strip()]
            if not keys:
                console.print(f"[bold red]❌ ERROR:[/bold red] File '[italic yellow]{file_path}[/italic yellow]' is empty.")
                return None
            console.print(f"[bold green]✅ Successfully loaded {len(keys)} private keys.[/bold green]")
            return keys
    except FileNotFoundError:
        console.print(f"[bold red]❌ ERROR:[/bold red] File '[italic yellow]{file_path}[/italic yellow]' not found.")
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
