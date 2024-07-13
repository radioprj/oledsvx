#!/bin/bash

dis=`lsb_release -i|cut -d: -f2 |tr -d '[:space:]'`
ver=`lsb_release -r|cut -d: -f2 |tr -d '[:space:]'`


if [ $dis == "Raspbian" ] || [ $dis == "Debian" ]; then

   if [ $ver == "11" ]; then

    echo "Instalacja pakietów dla OLED na bazie Debian 11"
    echo ""
    sudo apt-get update
    sudo apt install -y python3 python3-pip python3-smbus python3-dev i2c-tools python3-numpy python3-watchdog libgpiod-dev python3-libgpiod libjpeg-dev zlib1g-dev libfreetype6-dev liblcms2-dev libopenjp2-7 
    sudo python3 -m pip install --upgrade setuptools
    sudo python3 -m pip install --upgrade luma.oled
    sudo python3 -m pip install Pillow==10.3.0
    sudo python3 -m pip install psutil
    cp /opt/fmpoland/oledsvx/oledsvx.service /lib/systemd/system/
    echo ""
    echo "Instalacja zakonczona ...."
    echo ""
   fi

   if [ $ver == "12" ]; then

    echo "Instalacja pakietów dla OLED na bazie Debian 12"
    echo ""

    cp /opt/fmpoland/oledsvx/oledsvx.service /lib/systemd/system/
    echo ""
    echo "Instalacja zakonczona ...."
    echo ""

   fi

else

  echo ""
  echo " UWAGA - proces instalacji bibliotek systemowych przerwany"
  echo " Uzywasz dystrybucji systemu na bazie: $dis $ver"
  echo " Instalacja bibliotek przygotowana dla dystrubucji na bazie Debian v11 lub v12"
  echo ""

fi
