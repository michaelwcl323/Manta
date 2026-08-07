sudo apt-get update
sudo apt-get install tmux

# Install Python 3 and pip
sudo apt-get install python3 python3-pip python3-venv -y

curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
. $HOME/.cargo/env

sudo apt-get install libclang-dev

apt-get update
apt-get install iproute2

# Create Python virtual environment
python3 -m venv venv
source venv/bin/activate

cd benchmark
pip install boto3
pip install matplotlib
sudo -s
sudo apt install fabric
