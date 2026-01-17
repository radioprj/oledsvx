**Wykorzystanie wyświetlaczy OLED z SVXLink**

Wyświetlacze typu I2C: ssd1306 (0.96 cala), sh1309 (1.3 cala) lub ssd1309 (2.4 cala)
podłączone via I2C do Raspberry PI (RaspbianOS), Orange Pi Zero (ArmBian)

Nowa wersja kodu w Python3 napisana przez Arka SP2AM

**Instalacja pakietu dla systemów na bazie Debian 12**
-----------------------------------------------------
UWAGA gotowe obrazy mają już zainstalowany ten pakiet
więc nie ma potrzeby instalacji tylko nalezy skonfigurować.

sudo -s

cd /opt

jeśli nie masz katalogu "fmpoland" utwórz go poleceniem

mkdir fmpoland

cd fmpoland/

git clone https://github.com/radioprj/oledsvx.git

cd oledsvx/

**KONFIGURACJA**
-------------
Czytaj plik **[opis.txt](https://raw.githubusercontent.com/radioprj/oledsvx/refs/heads/main/opis.txt)** gdzie znajdziesz informacje o konfiguracji OLED
do współpracy z SVXLink



