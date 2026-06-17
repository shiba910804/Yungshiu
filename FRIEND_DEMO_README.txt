Yungshiu 專案展示啟動說明
==========================

一、朋友要開哪個檔案？

只需要雙擊：

  start_yungshiu.bat

start_dashboard.bat 只是舊檔名的捷徑，也會轉去啟動 start_yungshiu.bat。
兩個 BAT 不需要都開。


二、第一次啟動前需要什麼？

電腦需要先安裝 Python 3。
建議使用 Python 3.11 以上版本。

下載：
  https://www.python.org/downloads/windows/

安裝時請勾選：
  Add python.exe to PATH


三、啟動後會做什麼？

start_yungshiu.bat 會自動：

1. 檢查 Python 套件，缺少 Flask 時會用 requirements.txt 安裝
2. 啟動 dashboard
3. 建立/啟動每小時自動爬蟲排程
4. 打開瀏覽器：
     http://127.0.0.1:5000/


四、展示時如果瀏覽器沒有自動開

手動打開：

  http://127.0.0.1:5000/


五、如果要用手機在同一個 Wi-Fi 看

啟動視窗會列出類似以下網址：

  http://192.168.x.x:5000/

手機和電腦連同一個 Wi-Fi 後，用手機瀏覽器打開該網址。
如果 Windows 防火牆跳出提示，請允許 Private networks。


六、資料說明

壓縮包已包含 data/ 和 exports/，所以即使朋友當下網路不穩，
dashboard 仍可展示目前已爬到的基金與匯率資料。

如果朋友的電腦有網路，排程會每小時更新一次資料。
