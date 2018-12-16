@echo on

echo Processing >vgmout.txt

del *.exo
del *.bbc
del *.raw


for %%x in (*.vgm) do vgmconverter.py "%%x" -n -t bbc -q 50 -r "%%~nx.raw" -o "%%~nx.vgm.bbc" >>vgmout.txt
for %%x in (*.raw) do exomizer.exe raw -c -m 1024 "%%x" -o "%%~nx.raw.exo" >>vgmout.txt




