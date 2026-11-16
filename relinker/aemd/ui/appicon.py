# -*- coding: utf-8 -*-
"""aemd.ui.appicon —— 应用图标（标题栏 34px 小图 + 任务栏图标）。

为什么把图标以 base64 内嵌在源码里，而不是打包时带一个 png：
  · 这台机器上打包脚本传 `源;目标` 会被 PyInstaller 判成多个参数（build.ps1 第 6 行有记）；
  · onedir 打包后再找资源，容易踩 `sys._MEIPASS` 与 dev 两种路径；
  · 图标只有 4950 字节（68×68），内嵌最省事，dev / frozen 行为完全一致。

尺寸为什么是 68：标题栏那个位置是 34×34，`subsample(2)` 整数倍缩下去不糊。
"""

import base64
import tkinter as tk

#: 68×68 PNG（由 assets/shilian.ico 的 256px 帧缩放而来）
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAEQAAABECAYAAAA4E5OyAAATHUlEQVR42uWceZBdxXXGf6f73rfO"
    "vFm1LyPQhhSxGIyRjGyJxRiLAAKsOE7wggHjxC5X4hSxE5cLKCe24yXYcTn+IzGuFFRMIRzbMcbg"
    "JYBBgMVisQ2LhLaRkDSSZn3bXbo7f9w3b2akWSRhl5F0q1pTpXdv3+7T53zn66/Pe3BE1zqdtOP2"
    "UrDKA2SyG2XyjnC1xqmnXtwU2Pg0gyzSyrZZJI1NPnsLXSKIcWL7RPxtXqw7d+58cM/oxV1vjsEg"
    "ww/OOOXC9zjHdYK7UERNE6WOxNh//MtZrLWD4J4GdTdB9q49e+4rwy0Kbqsv9BEYJDHGlHkXnOnB"
    "V0TUpSKCdRacdTgxDvfWN4mgQJQojSBYZ17Fmtv27HjkByPm7yYxSGKMqXNX3aC1+jclOmttZJMH"
    "5ThxjdF+As6COBHliSisjb+/J9f9CTo7w0ONcsjkVnnwSDyjY9XnlPa/7FyMc86AHM+AOjqGEKu0"
    "7xkb/sKLgrW7dj0ZjMRJNdozHomnzX3X9aK9L1sbxtY6B+jh+4/3hgLnmTgMtfiXGJ26E7Cwru75"
    "ekSY2GmzVywT7f/YWSvJZyKcmJd2zkRKp5ZlC3MGSv33P57YoNPVPGSpA5xo/1siKgO2Fk4nimeM"
    "6S2eNZFVuC9O61g1D9ZbuEWpxDK32alzV14soi90NjqBMGMyDmatUl5erPtMYqVOqWOIOLlJwOGc"
    "wzlOjoa2NnZO7F80daxqhvVGwXqzYMGlBSdutXFGHKJP5EA5pIlz1ory2jLOrATwAMph/zKF3+6s"
    "dcchz3izqdiJwznrVgD3KQBj9WIRBTjDyXkJIkvqHmJxbYoxqf3J4CHisDhsS90ggssN4cxJeVnA"
    "iV83yGjOcdJ5yEhuUjfIHyTLj/v+oxM3JoDD3/+wvRE+c8weIkolW0YHzlmcc2Dd+PKNyGHPJP0I"
    "qkaNrLW1Nv6YlFIopZCa1aytvfvNeoi1IOKOqjMRQUThnCWolDHGoLTG91N4nof2xnY+YwxxFBJV"
    "q1hr0NojlU6jlKJSqVKtVlFKkU6nSKfTpFIplJLhpXKJ11jrCKoBpVKZKAwRpcjlsvgpH2uOxjCJ"
    "MWxtUbxjiTkRhcNRLQ3ip9PMW3ga8xacxvTZHRSaWxOj+P4YHieYOCIIqvT39rBvdxc7Xn+F7Vte"
    "IwgCFi5awPIV7+DMs5bRMW8uDQ0NZGrGciP6EgRjDJVqlYMHDvL6lm08++wmfvvE0+zdu5d8Pk8q"
    "5WOM/cOHjFKaMKgiIpx53kqWr7qEmR2n4KfSOGux1iSimnOH44ireZYSOuYvgrevoFypMti9g9nt"
    "PhdevIq2tjzOQRwnnjte9hMBpUDrhVx08XKM+SBdO/ey/p4f8d933UN3934KhUJtJ+KOOGQEoH3m"
    "O27R2r/V2igG8cZ7UClNuVRk5px5rFn3YU5d/CdYawmDav2lcgSKgQjEsUUE3rZ0FssWzSSdhlLJ"
    "EEXRqH4m6m/onclfIZNJkc0KO7bv5V+//h3W3/M/FAqNCbFwdhykd0bE09bFGw7s3rjSG57u+B7i"
    "HCitqJSLLDnzHK7+0CfINxaolEuIgMgwsE22GiIKYx3ZbIrz39bBrGlNVIOASsWhlEJrPWrCE/Un"
    "NYAeusIwolKxTJ02jdu//UUWLDyVr3/1m2QyGURknL5Gz9sbOemx313zjGKRM96+gg9c/+kkfssl"
    "lBoW3Ky1iAja80ZN6hB+TGwMvue4aPkCWpuyVKpRzRC1Zamttgik0ym0l4xLxlgkayGKYuI4rmcb"
    "z9MEQUhQdfzN311HoVDg85+7lXxDfsI0PfTZpBgiShFUK8w5ZQFrr/04xgwPYGgVRYRcvoEoChns"
    "76M40IcbmS5HzMbzU1y1ZgWthQzVaoBSAk5qhEPjeQqFITSObdu66O/rTTLWiNk4B6mUTzaXZfr0"
    "abS0ZKhUoFoN0FrVx7a/u8p1119DV9duvvPt79Lc3Iwx5s0RM2cMqXSatdfeSCaTpVqt1F9orSWV"
    "TmONYeOjv+K5jY9ysHsf1XJpVFYA0FrT29vHP3z+ZhZ0NNMzYNDp7PBdxqJtmd6eCpu2VqiUK2x5"
    "tZPvffOfx84Gnofve0yfMZ13r17Jtdd+gI55MxgYCOrj01rR1xty82c/xabfbeLJJzbS0NCAteNn"
    "Hw2Qa5y5WkStxhk7UnhWSlEuFzn/oss4550XUCkVUbVwsNaSyWTp3ruLu//jdp546AH6ew5iTIwo"
    "qce3iKCUJgiqLFq8kC995RZiUujyLtL7HyXV/Rsyb9xHdtc9ZLruJrt7PQP9fbxe7mDGzBkMDvSx"
    "fXMn6UxmFG5Ya4miiP3d+3l8w5Pc99Of09beztlnL6Vajev3GWvJZHxmz57DT370U7RWh0SCcyJK"
    "OWxXZfCNO+rETMloDBERoiiiuXUKy1e/lyCoIvUwsaQzGd7YtZ3/+vZXGOzvJZtrTLxiDCBUSgiC"
    "kBtu/AgtU/IUt26i5aXPIlHvcDyJhxOPvBJW5+9nb3kp+4PFnH/hpTz/9BME1TJK6eG+JWG1mYwm"
    "l8szOFjk05+8mf6+AW686S/p60vCR2vN4GDAO1eezXvf9x5+8uP7aGpqOjx07PDZ7SEY4mrZAMKg"
    "yuLTz6alfSpxFNaRWmmParnMvd//DsWBPnL5PNbGOGtq6W10P0G1SkfHHC66aDXFsiO372dI1ItL"
    "tWP9JoxXIFY5jPjEeIgzeBIThhFtU2eweNlZBNVKbV9T69s5XHJUSRxH+L5HodDIF2/7Mg8/9FsK"
    "hfSISSdGvOqaK9BaHTbGpFkOOZc5HGqUUixcekYd+YdANJ3O8PhDP+eNnVvJ5nJjANXo/UalWuWc"
    "c89m6rQWolCwuTmIqYAN0RjyKUdzztGUE1Iq4oV4JXvsfFIS4BzMP+30SfmNtbY2Wcc3vvYtKuWw"
    "nu2SbYHl9DNOZ9bsWQRhOG5/3tj5WLAmJpvPM3XGbOI4SjpwDs/zGOjr4dknHiadyWCtmYThOpwz"
    "LFu2FKVB4oDKnHVINEB23/30FiM2bQ/ZuDlk68EM5679LMXMYtJuAATiOGb67A6yuXyCT6LGfZ8x"
    "MQ0NeZ595lkefngDay67gP7+KlproihiypRmFi6cT9fOnaTTqRpkuhERMsJDRorRACY25BoK5BsL"
    "WGPq4eL5Kfbs2k5/zwGU9rB2YpHeWofWHnPnzU02kRicl8X5eYhL5NOwdJbm8nPS/Pl5lnNTD3K2"
    "/8shH8XYmHxjgVy+se6Jk4npcWzY+NunUHo0q/V8mDNnNia2Q1F32LzH5SHWGrLZHJ6fGqbINffr"
    "OdBNHEek0ulJd5XOWXzfp6lQwBpwOoNX3kN2x104FJ5yzGwR5rZ7eBrC4Bk89xh518uG8Aoyrorn"
    "+TVvPJI9l0NrxY7tO4jD0dRfBJqam2o725H9uFEFMROXnoxBl20cH5U6k9D7ZAsvdshDCkg8iK/B"
    "OCgFjt6io2QaqLpG5ukX0BLjnKDqFH3ydw7xnzCKxrw7SbsTVwgxlh+KQBwEiZuOsLK1lsbmVpSu"
    "pcDJGgkOJDoH4GKs30xx8d8TtZ7HngHNYNnia6GQhawqkpMBdpolGOcl4zAxURigkEnfJw6ssUxp"
    "bychuG4UTJaKpWSRDxvnSIFoRO3U8KpqisVBgkqZfKEJEydkx8QR02fOIZPNY2pgO9G6KRGiMGTv"
    "nn0oDQ5B2YiwbQWe6Wfjk//Ojq5+PE+Ty6U455x30K0W0xkuxydAlEel1EexOIBoVTtwm+yA0nLm"
    "WWccJj8aA7t2766P2R1OQ2oYYi2o4RVNuIaiNNjHwf17aGptw0QJA42ikPbps1iw5HSee2oDuXwD"
    "boK0i3NY6+h8qRNhLTiL02kaXvk62R13cvVZ7cRnNDBQtgwGCu3tR+OzW82n17ST8TQH9++hXCwm"
    "mDUB7ZYaAZw5awYXXryactnWabzneQwOBmzdsg3f9xM8cm50Tc1oDDmEXYoiDAK2b34ZrT1c3YaJ"
    "trDq0qtIpdL1DDSRZpFK+Tz91DMMDkZoL40KS6QOPIZNt1OONJGBxqxidotjureTt3m/5IL03eAs"
    "Smu2b345IYaTqV3aY6Cvn+s+9hFOOXU6QRDWaX46rdmy5XW2bd2epNxxDKsmyg6e79O5aSPVckKb"
    "h0A1qFaZPW8Ba97/YSqlQXBulBRwKGHKZrN0vvQyv3tmE7mcJlYZ4sJpqGA/2pZRNsQYQ2iEkAxl"
    "10STdJPxYkqlMi9v2ojv++NmNKUE3/fo3ruPyy5/Hx//q48y0D9MzKx1pDPCg/c/SHFwYFx5or65"
    "y+SnrRaR1TWzqSGP8Tyfgwf20T5tJh3zFyfSYU13iKOIeYuWks018OqLzxKGVTzPQ5RC1QSjoaY9"
    "TaVSoThY4sqr1xCGjrj1HFBp0ClQPmAS9mqqpFTMs+FF7M+9k+ef/AVPP/prsvl8ffJJUyiV6CaV"
    "SpW+vn7WXnMlt3/rq3h+CmNcjTtZ0mmfXV3dfOEfb8VYm0gOI2RrEVEO21Utdd/hDSGKk7EyqUMr"
    "j0ce+BHLzl6O9ry6ECQiBJUK77pkLbM65vN/P7uX7Zs7Ccql0VrIiPj+yfp7ueLKNVx59fvoHyhQ"
    "WvQpxAJxBRX1osIe+va+zotdhm3RAgb3b+aHd93BYP8AxXJljE2jIuWnWLBwPjd84mP82QeuwVhL"
    "GJrapMEYS2ur5p9u+x67du2mtbV1zK1GDUKSsGxuX3aLaO9WZ+PDNFWlFOVSkXe95wrWXnsTpcGB"
    "ugRQlwGyOaw17Nm5ja7tWxjs7z0MV5QSqtWAU06Zx82f+RhaJekx2UErnPKS0QgUizHlcokXn3+e"
    "zk1PkUpnEueV4VTirKXQ1MTi0xZx5lln0NaWo78/HKXDRlFMW1uWXzz4GDd89OPjhZ0RUdpau6H/"
    "wAsrJzXI0AuqlRJXfeivefclVzLQ11MPncS6FkRIpdJo3x8bZOu6bJX25hQXnDcfa13d4+pcGofW"
    "GqUUvq9JZ8bngEPqfLkSE0fxKGyIo5jm1iyvvbKddVd/kL7ePtLp1FgHX6MM4o0nth66801nMvz0"
    "7v9EKcWKC9ZQKRex1qCUruskYRjggur4jLJmlN37Kjz2zHZWnDUXz9NEkamL1TXxjNhYgjBmYNCO"
    "VV87SiwSkREAmpz2tbZneWHTq9x4/Sc5eOAA+Xx+gl358NwTUM1NXQ1qtUuWWo11v0hy9PjCM48T"
    "BhUWLDmDdDZPHIV1iX9YgR+n1UrCPa050Ftk34EiU1pyFBqzIIo4ig+b7NBx5Xjt0GPMXC5DY8Hj"
    "f3/8S266/pPs27tvEmPUCpKd6woq3XfUDNK+GlGrE4Y2Xip2iBI832dz53O89uLvyGazTJ81l2wu"
    "X0P0WrWJm7D6D2rcpBo69vaElEplMp5l2oxGfM/DOVU/2x06ihir1YE15dPQ4JFOe3S+9Aq3fuFL"
    "fONfbieOI7LZ7IR6zVCWwdmuoLL/Du/oio+SQeTyDbzRtZW7vvs1Tlm0hDPOXcm8BUtobptKOpNF"
    "a2/cggDnHMYaqtUKfQe7ef3Vl3hqw0MoW+VPL7+M1Re+m/nz59PS2koul2YC+QNjIAhCdu7s4sXn"
    "X+SB+3/Or3/1MH19/TQ1FepnyUddtNDcvvQWRN/qXDzByd2h8atAIKhWMHFMNpensamFQnMb2Vx+"
    "QmUrqJYZ7O9loLeHaqWEn0rhnFAql8lk0kyZMoVp06fRPqU9wYaxKt+co1KpcrCnhz1vvMHBAz04"
    "52hoyON53tEYwogo7azZ0H/w5ZXDp//q6OothkoYUukskk4m2ntgPwe690643xguY9BozyObL9T7"
    "amlpwTlLT08P+/Z1Y4yZUG8ZOunzfZ9CoVA3eBybYyqbOUQgUsdUH+KsqR8Te76Hhz9xkcuIfxJF"
    "LR4RAnbEmYs/qY7q3PBpnzHxm6wi4vddQeRGDPDNFkq6Yyh8+b1WEMHJW2PGGBVEjkD9EVeFP3bJ"
    "exJ2ZuRBVT8n7WXr6uKIOlW21z5QJ1/YJGEh2G11D4nFvuCsqR5zqjnuY8YBPDUUQAqwDc0LHxel"
    "ltdIhD6J0BRwsbJyWn//5q0KVqka9bwTEDeiuOrEb84iSpzjN/39m7eSFEEkpHjKlKX5Shh2IjK7"
    "FlfqJPCQWBDPOnNxqX/rr2GdlhHaqmlsXnAtou50zkSQFMOfwNESi2jPGnNvaeD1dUPQoUfEkg6r"
    "Pc+l0k2LRHlngYlOYCwxoDyc6cKwNop6K0N2OHTCKgzy93spfb6Inu+cjY7Tb3NP6BmI8oA+B2vK"
    "g1u31OZnGcMDBAaiqDD9h54JT1eilyR6vI2pS8DHbTaxNdlC49wO69zllYGtz9RsYEedyxxaOER5"
    "fxAFvT/wUi2hiFsuojM1Y5ih79EfH4nE1X+zQJKvX4g4d4/z43WV3u2vDWHnkXyrZej/XWPjvMVW"
    "yd86eL8S1Tae4PvW3aeAcyYEHnLOfrM8sOOBkYnkaH9Qpf5QQ8OidqPNSpxdIbAYXDtvWeuIOMeA"
    "KHYAG7WVRwcHt24e60diOLafqzkhss0RzeP/AWirlOq18fesAAAAAElFTkSuQmCC"
)


def photo(master=None, scale=2):
    """返回一个 Tk 图片对象；调用方**必须**自己留住引用（否则会被回收成空白）。

    scale=2 -> 34×34（标题栏）；scale=1 -> 68×68（任务栏 / 关于框）。
    Tk 8.6 的 PhotoImage 原生支持 PNG，不需要 Pillow。
    """
    data = base64.b64decode(PNG_B64)
    img = tk.PhotoImage(master=master, data=data)
    if scale > 1:
        img = img.subsample(scale, scale)
    return img
