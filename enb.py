import discord
from discord.ext import commands
from discord import app_commands, Intents, Embed, Color
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta # timedelta 추가
import os
from dotenv import load_dotenv
import re

# .env 파일에서 환경 변수 로드
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

if not DISCORD_BOT_TOKEN:
    print("오류: DISCORD_BOT_TOKEN이 설정되지 않았습니다. .env 파일을 확인해주세요.")
    exit()

# --- 유틸리티 함수 ---
def get_korean_weekday_name(date_obj):
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return weekdays[date_obj.weekday()]

def cleanup_component_text(component_text):
    text = component_text.strip()
    if text == "(조합원),":
        return "(조합원)"
    return text

def refine_final_menu_string(raw_menu_line):
    line_after_won_char_fix = raw_menu_line.replace("￦,", "￦ ")
    parts = line_after_won_char_fix.split(", ")
    refined_parts = []
    for part_text in parts:
        current_part = part_text.strip()
        if current_part.startswith("￦ ") and current_part.endswith(","):
            refined_parts.append(current_part[:-1])
        elif current_part == "(조합원),":
            refined_parts.append("(조합원)")
        else:
            refined_parts.append(current_part)
    return ", ".join(refined_parts)

def parse_flexible_date_str(input_str, current_year=None):
    if current_year is None:
        current_year = datetime.now().year
    input_str = input_str.strip()
    try: return datetime.strptime(input_str, "%Y-%m-%d").date()
    except ValueError: pass
    if len(input_str) == 8 and input_str.isdigit():
        try:
            year = int(input_str[:4]); month = int(input_str[4:6]); day = int(input_str[6:8])
            return date(year, month, day)
        except ValueError: pass
    if 3 <= len(input_str) <= 5 and input_str.count('-') == 1 :
        try:
            dt_obj = datetime.strptime(input_str, "%m-%d")
            return date(current_year, dt_obj.month, dt_obj.day)
        except ValueError: pass
    if len(input_str) == 4 and input_str.isdigit():
        try:
            month = int(input_str[:2]); day = int(input_str[2:])
            return date(current_year, month, day)
        except ValueError: pass
    raise ValueError(f"'{input_str}'은(는) 인식할 수 없는 날짜 형식입니다.")


# --- ID 패턴 기반 웹 크롤러 ---
RESTAURANT_CODES = {
    "18": "한빛식당", "19": "별빛식당", "20": "은하수식당"
}
MEAL_TIME_CODES = {
    ("18", "9-17"): "아침", ("18", "8-16"): "점심", ("18", "10-18"): "저녁",
    ("19", "7-14"): "점심",
    ("20", "6-12"): "아침", ("20", "13-25"): "점심"
}

def fetch_menu_by_specific_id_pattern(target_dt: date): # 입력은 조회할 날짜의 date 객체
    print(f"[{datetime.now()}] '{target_dt.strftime('%Y-%m-%d')}' 메뉴 크롤링 시작...")
    
    # --- 주차(week) 계산 로직 추가 ---
    today_dt = date.today()
    
    # 대상 날짜의 월요일과 오늘 날짜의 월요일을 기준으로 주 차이를 계산하는 것이 더 정확할 수 있음
    # target_dt의 ISO 주에서 월요일 찾기
    target_monday = target_dt - timedelta(days=target_dt.weekday())
    # today_dt의 ISO 주에서 월요일 찾기
    today_monday = today_dt - timedelta(days=today_dt.weekday())
    
    # 두 월요일 사이의 일 수 차이를 계산하고, 7로 나누어 주 차이를 구함
    delta_days = (target_monday - today_monday).days
    week_offset = delta_days // 7 # 정수 나눗셈
    
    print(f"  대상 날짜: {target_dt}, 오늘 날짜: {today_dt}")
    print(f"  대상 날짜의 월요일: {target_monday}, 오늘 날짜의 월요일: {today_monday}")
    print(f"  계산된 week_offset: {week_offset}")
    # --- 주차 계산 로직 끝 ---

    chrome_options = ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36")

    driver = None
    parsed_menus = {name: [] for name in RESTAURANT_CODES.values()}
    target_weekday_d_code = str(target_dt.weekday()) # 조회하려는 날짜의 요일 코드 (0:월 ~ 4:금)

    # 주말 예외 처리 (패턴에 주말이 없으므로, 해당 요일 코드로 조회 시 어차피 안나옴)
    # 하지만 week_offset 계산에는 영향을 주지 않으므로, 먼저 크롤링 시도 후 결과로 판단.
    # 만약 주말에 "운영 안함"을 명시적으로 표시하고 싶다면, 여기서 target_dt.weekday() >= 5 일때 바로 반환 가능.
    # (현재 로직은 아래에서 ID 못찾으면 "정보 없음"으로 처리)

    try:
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # 계산된 week_offset을 URL에 적용
        url = f"https://www.cbnucoop.com/service/restaurant/?week={week_offset}"
        print(f"  접속 URL: {url}")
        driver.get(url)

        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "menu-result")))
        page_source = driver.page_source
        soup = BeautifulSoup(page_source, 'html.parser')
        found_any_menu_for_day = False

        for (res_code_a, bc_val), meal_name in MEAL_TIME_CODES.items():
            restaurant_name = RESTAURANT_CODES.get(res_code_a)
            if not restaurant_name: continue
            
            # ID 생성 시 사용되는 요일 코드는 target_dt의 요일 코드 (0~4)
            # 웹사이트가 week 파라미터로 해당 주의 메뉴를 보여주면,
            # 그 주의 테이블에서 target_dt의 요일에 해당하는 열을 읽어야 함.
            target_element_id = f"table-{res_code_a}-{bc_val}-{target_weekday_d_code}"
            
            # print(f"    검색 시도 ID: {target_element_id} ({restaurant_name} - {meal_name})") # 너무 많은 로그 방지 위해 주석
            menu_element = soup.find(id=target_element_id)
            
            if menu_element:
                components_for_this_meal = []
                p_tags = menu_element.find_all('p')
                if p_tags:
                    for p_tag in p_tags:
                        raw_text = p_tag.get_text(strip=True)
                        if raw_text and raw_text != '-':
                            components_for_this_meal.append(cleanup_component_text(raw_text))
                else: 
                    full_text_no_p = menu_element.get_text(separator='\n', strip=True)
                    if full_text_no_p and full_text_no_p != '-':
                        lines = full_text_no_p.split('\n')
                        for line in lines:
                            stripped_line = line.strip()
                            if stripped_line and stripped_line != '-':
                                components_for_this_meal.append(cleanup_component_text(stripped_line))
                                
                if components_for_this_meal:
                    raw_joined_line = ", ".join(components_for_this_meal)
                    final_menu_line = refine_final_menu_string(raw_joined_line)
                    full_menu_entry = f"[{meal_name}] {final_menu_line}"
                    parsed_menus[restaurant_name].append(full_menu_entry)
                    found_any_menu_for_day = True
                # else: print(f"    ID {target_element_id} 내용 없음 ({restaurant_name} - {meal_name})")
            # else: print(f"    ID {target_element_id} 찾을 수 없음 ({restaurant_name} - {meal_name})")
        
        for res_name in RESTAURANT_CODES.values():
            if not parsed_menus[res_name]: # 해당 식당에 추가된 메뉴가 하나도 없다면
                 # 주말이거나, 해당 요일에 운영 안하거나, 메뉴가 없는 경우
                 if target_dt.weekday() >= 5: # 토, 일
                     parsed_menus[res_name] = [f"{get_korean_weekday_name(target_dt)}요일은 주말 메뉴 정보가 제공되지 않습니다."]
                 else: # 평일
                     parsed_menus[res_name] = [f"{get_korean_weekday_name(target_dt)}요일에는 해당 식당의 메뉴 정보가 없습니다."]

        if not found_any_menu_for_day and target_dt.weekday() < 5: # 평일인데 어떤 메뉴도 못 찾았다면
            print(f"  {target_dt.strftime('%Y-%m-%d')} ({url}) 전체 메뉴 항목 없음.")
            # 이 경우, 위 로직에 의해 각 식당은 "정보 없음"으로 채워져 있을 것임.
        
        print(f"  크롤링 완료. 접속 URL: {url}")
        return parsed_menus
    except Exception as e:
        print(f"  크롤링 오류 ({url}): {e}")
        return {"오류": f"크롤링 중 오류 발생: {e} (URL: {url})"}
    finally:
        if driver: driver.quit()

# --- 디스코드 봇 설정 (이하 동일) ---
intents = Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'[{datetime.now()}] 봇 준비 완료: {bot.user.name}')
    try:
        synced = await bot.tree.sync()
        print(f"  {len(synced)}개 슬래시 커맨드 동기화 완료.")
    except Exception as e:
        print(f"  슬래시 커맨드 동기화 실패: {e}")

@bot.tree.command(name="학식", description="충북대학교 학식 메뉴를 보여줍니다.")
@app_commands.describe(날짜="조회할 날짜 (예: 2025-05-28, 05-28, 0528, 20250528). 입력 않으면 오늘.")
async def get_menu_slash(interaction: discord.Interaction, 날짜: str = None):
    await interaction.response.defer(ephemeral=False)
    
    target_dt: date 
    current_year = datetime.now().year

    if 날짜 is None:
        target_dt = date.today()
    else:
        try:
            target_dt = parse_flexible_date_str(날짜, current_year)
        except ValueError as e:
            embed = Embed(
                title="⚠️ 입력 오류", 
                description=f"날짜를 이해할 수 없습니다: {e}\n"
                            "지원 형식: 'yyyy-mm-dd', 'yyyymmdd', 'mm-dd', 'mmdd'",
                color=Color.red()
            )
            await interaction.followup.send(embed=embed)
            return
    
    try:
        menu_data = await bot.loop.run_in_executor(None, fetch_menu_by_specific_id_pattern, target_dt)
        if "오류" in menu_data:
            await interaction.followup.send(embed=Embed(title="🚫 메뉴 조회 실패", description=menu_data["오류"], color=Color.orange()))
            return

        embed_title = f"📅 {target_dt.strftime('%Y년 %m월 %d일')} ({get_korean_weekday_name(target_dt)}) 충북대학교 학식 메뉴"
        embed = Embed(title=embed_title, color=Color.random())
        if not menu_data: embed.description = "조회된 메뉴 정보가 없습니다."
        else:
            has_actual_menu = False
            for restaurant_name, items in menu_data.items():
                if items and not (len(items) == 1 and ("정보가 제공되지 않습니다" in items[0] or "정보가 없습니다" in items[0])):
                    has_actual_menu = True
                menu_str = "\n".join(f"- {m}" for m in items) if items else "- 정보 없음"
                embed.add_field(name=f"🍽️ {restaurant_name}", value=menu_str, inline=False)
            if not has_actual_menu and target_dt.weekday() < 5: # 평일인데 실제 메뉴가 없다면
                # found_any_menu_for_day가 False였고, target_dt가 평일이었다면 이미 각 식당에 "정보 없음" 메시지가 들어갔을 것.
                # 여기서는 추가적인 메시지를 띄우기보다는, 필드 내용을 신뢰.
                # 만약 모든 필드가 "정보 없음" 류의 메시지만 있다면, 그것이 결과.
                # 좀 더 명확한 메시지를 원한다면, has_actual_menu 외에 다른 플래그 필요.
                # 여기서는 일단 현재 로직 유지.
                pass
            elif not embed.fields and not embed.description: embed.description = "조회된 메뉴 정보가 없습니다."
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"  /학식 명령어 오류: {e}")
        await interaction.followup.send(embed=Embed(title="🚨 시스템 오류", description=f"예상치 못한 오류: {e}", color=Color.dark_red()))

if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
