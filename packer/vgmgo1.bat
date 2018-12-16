@echo on

echo Processing %%1 >vgmout.txt

del %%1.exo
del %%1.bbc
del %%1.raw


vgmconverter.py "%%1.vgm" -n -t bbc -q 50 -r "%%~n1.raw" -o "%%~n1.vgm.bbc" >>vgmout.txt
exomizer.exe raw -c -m 1024 "%%1" -o "%%~n1.raw.exo" >>vgmout.txt




