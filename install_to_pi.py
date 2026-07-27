import os
import sys
import subprocess
import re
import socket

# Ensure paramiko and scp are installed
try:
    import paramiko
    from scp import SCPClient
except ImportError:
    print("Installing required Python libraries (paramiko, scp)...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "scp"])
    import paramiko
    from scp import SCPClient

# Set console encoding to UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

def find_pi_ip():
    print("Searching for Raspberry Pi on the network...")
    
    # 1. Try resolving hostname
    try:
        ip = socket.gethostbyname("greenhouse.local")
        print(f"Found Pi via hostname (greenhouse.local): {ip}")
        return ip
    except Exception:
        pass

    # 2. Try parsing ARP table for Raspberry Pi MAC addresses
    try:
        output = subprocess.check_output("arp -a", shell=True).decode('utf-8', errors='ignore')
        # Common Raspberry Pi MAC prefixes
        patterns = [r"b8[-:]27[-:]eb", r"dc[-:]a6[-:]32", r"e4[-:]5f[-:]01"]
        for line in output.splitlines():
            for pat in patterns:
                if re.search(pat, line, re.IGNORECASE):
                    match = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", line)
                    if match:
                        ip = match.group(1)
                        print(f"Found Pi via MAC address scan: {ip}")
                        return ip
    except Exception:
        pass
        
    return None

def progress(filename, size, sent):
    sys.stdout.write(f"\r{filename}: {sent}/{size} bytes ({float(sent)/float(size)*100:.1f}%)")
    sys.stdout.flush()

def main():
    ip = find_pi_ip()
    if not ip:
        ip = input("Could not find Pi automatically. Please enter Pi IP address (e.g., 10.139.84.202): ").strip()
        if not ip:
            print("No IP entered. Exiting.")
            sys.exit(1)

    password = input("Enter Pi password [default: greenhouse2026]: ").strip()
    if not password:
        password = "greenhouse2026"

    # Resolve paths relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_dir = os.path.join(script_dir, "pi")
    remote_dir = "/home/pi/greenhouse"

    if not os.path.exists(local_dir):
        print(f"Error: Directory '{local_dir}' not found. Make sure this script is in the project root.")
        sys.exit(1)

    print(f"\nConnecting to Pi ({ip})...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(ip, username="pi", password=password, timeout=15)
    except Exception as e:
        print(f"Authentication failed or timeout: {e}")
        print("Note: If you already ran the installer on this card, the password has been randomized.")
        print("You can read the randomized password from INITIAL_PASSWORD.txt on the SD card boot partition.")
        sys.exit(1)
        
    print("Connected successfully.")

    print("Creating remote directory...")
    ssh.exec_command(f"mkdir -p {remote_dir}")

    print("Uploading files...")
    with SCPClient(ssh.get_transport(), progress=progress) as scp:
        items = ["scripts", "systemd", "portal", "mosquitto", "install.sh"]
        for item in items:
            local_path = os.path.join(local_dir, item)
            print(f"\nUploading {item}...")
            scp.put(local_path, remote_path=remote_dir, recursive=True)

    print("\nFiles uploaded successfully.")
    
    print("Running installation and selftest...")
    # Fix line endings if uploaded from Windows and run install.sh
    cmd = (
        f"find {remote_dir} -type f \\( -name '*.sh' -o -name '*.py' -o -name '*.service' -o -name '*.conf' \\) "
        f"-exec sed -i 's/\\r$//' {{}} + && sudo bash {remote_dir}/install.sh && sudo bash {remote_dir}/scripts/selftest.sh"
    )
    
    stdin, stdout, stderr = ssh.exec_command(cmd)
    
    while True:
        line = stdout.readline()
        if not line:
            break
        try:
            print(line, end="")
        except Exception:
            print(line.encode('ascii', errors='replace').decode('ascii'), end="")
            
    err = stderr.read().decode('utf-8', errors='replace')
    if err:
        print("\nSTDERR:")
        print(err)
        
    print("\nFinished running script.")
    ssh.close()

if __name__ == "__main__":
    main()
