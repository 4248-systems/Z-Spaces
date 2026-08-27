mkdir tmp
git clone https://github.com/srdg-net/Spaces-Hub tmp
mkdir -p spaces
cp -r tmp/spaces/. spaces/.
rm -rf tmp