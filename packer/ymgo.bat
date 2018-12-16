@echo on

echo Processing >ymout.txt

for %%x in (*.ym) do ..\..\ym2sn.py "%%x" >>ymout.txt





