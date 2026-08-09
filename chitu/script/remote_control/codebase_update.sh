cd narwhal || (echo "Repository narwhal not found. Please run: fab cloudlab-install" && exit 1)
git fetch
git checkout experiment1
git pull

