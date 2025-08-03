import discord
from discord.ext import commands
import gspread
import itertools
import random
import numpy as np
from scipy.optimize import linear_sum_assignment
import os
from dotenv import load_dotenv
from urllib import parse
import requests

# .env 파일에서 환경 변수 로드
load_dotenv(dotenv_path="./.env")

# 봇 기본 설정
intents = discord.Intents.default()
intents.message_content = True
app = commands.Bot(command_prefix='$', help_command=None, intents=intents)


### League of Legends ###


request_header = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept-Language": "ko,en-US;q=0.9,en;q=0.8,es;q=0.7",
    "Accept-Charset": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://developer.riotgames.com",
    "X-Riot-Token": os.getenv('riot_api_key')
}

def getNameTag(summonerName):
    """입력된 소환사 이름을 이름과 태그로 분리합니다."""
    splitted_name = summonerName.split('#')
    if len(splitted_name) == 2 and splitted_name[1]:
        gameName, tagLine = splitted_name
    else:
        gameName = summonerName
        tagLine = "KR1"
    return gameName, tagLine

def get_PUUID(gameName, tagLine):
    """Riot ID로 PUUID를 조회합니다."""
    api_url = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{parse.quote(gameName)}/{parse.quote(tagLine)}"
    return requests.get(api_url, headers=request_header).json()

def get_summonerinfo_by_puuid(puuid):
    """PUUID로 소환사 정보를 조회합니다."""
    api_url = f"https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
    return requests.get(api_url, headers=request_header).json()

def get_league_info_by_summoner_id(summoner_id):
    """소환사 ID로 랭크 정보를 조회합니다."""
    api_url = f"https://kr.api.riotgames.com/lol/league/v4/entries/by-summoner/{summoner_id}"
    return requests.get(api_url, headers=request_header).json()

# 티어별 색상 코드
rank_color = {
    'IRON' : 0x413530,
    'BRONZE' : 0x6B463C,
    'SILVER' : 0x8396A0,
    'GOLD' : 0xBB9660,
    'PLATINUM' : 0x5CB9AE,
    'EMERALD' : 0x035B36,
    'DIAMOND' : 0x265BAB,
    'MASTER' : 0xB84EF1,
    'GRANDMASTER' : 0xBA1B1B,
    'CHALLENGER' : 0xD7FAFA 
}

# ----------------------------------------------------------------
# 팀 빌딩 핵심 로직 (기존 코드와 동일)
# ----------------------------------------------------------------

def get_lol_data_from_sheet():
    """
    구글 시트에서 설정값과 플레이어 DB를 불러옵니다.
    """
    try:
        gc = gspread.service_account(filename='service_account.json')
        spreadsheet = gc.open("리그오브레전드 팀 구성")
        
        settings_sheet = spreadsheet.worksheet("설정")
        all_settings_values = settings_sheet.get_all_values()

        tier_scores = {}
        position_weights = {}
        current_category = ""

        for row in all_settings_values[1:]:
            if len(row) < 3: continue
            category_cell, key_cell, value_cell = row[0], row[1], row[2]

            if category_cell.strip(): current_category = category_cell.strip()
            
            if key_cell.strip() and value_cell.strip():
                key = key_cell.strip()
                try:
                    value = float(value_cell.strip()) 
                except ValueError: continue

                if current_category == "티어점수": tier_scores[key] = value
                elif current_category == "포지션가중치": position_weights[key] = value

        player_db_sheet = spreadsheet.worksheet("플레이어_DB")
        player_db = player_db_sheet.get_all_records()
        
        return tier_scores, position_weights, player_db
    except Exception as e:
        print(f"시트 데이터 로딩 오류: {e}")
        return None, None, None

def balance_teams(grouped_players, solo_players, tier_scores, position_weights, player_db):
    """그룹 플레이어와 솔로 플레이어를 받아 최적의 팀 밸런스를 맞춥니다."""
    
    positions = ['탑', '정글', '미드', '원딜', '서폿']
    all_player_names = grouped_players + solo_players

    participants = [p for p in player_db if p.get('이름') in all_player_names]
    
    if len(participants) != 10:
        found_names = {p['이름'] for p in participants}
        missing_names = set(all_player_names) - found_names
        return None, f"다음 플레이어를 DB에서 찾을 수 없습니다: {', '.join(missing_names)}"

    score_matrix = {}
    for player in participants:
        player_name = player['이름']
        player_tier_str = str(player.get('티어', '')).strip()
        tier_score = tier_scores.get(player_tier_str, 0)
        
        score_matrix[player_name] = {}
        for pos in positions:
            proficiency = player.get(pos, 1)
            weight = position_weights.get(pos, 0.5)
            calculated_score = tier_score * (1 + (proficiency - 1) * weight)
            score_matrix[player_name][pos] = calculated_score
            
    best_combination = []
    min_score_diff = float('inf')
    
    needed_for_group = 5 - len(grouped_players)
    
    for extra_players_tuple in itertools.combinations(solo_players, needed_for_group):
        extra_players = list(extra_players_tuple)
        
        team_a_names = grouped_players + extra_players
        team_b_names = list(set(solo_players) - set(extra_players))
        
        team_a_participants = [p for p in participants if p['이름'] in team_a_names]
        team_b_participants = [p for p in participants if p['이름'] in team_b_names]

        cost_matrix_a = np.array([[-score_matrix[p['이름']][pos] for pos in positions] for p in team_a_participants])
        row_ind_a, col_ind_a = linear_sum_assignment(cost_matrix_a)
        team_a_score = -cost_matrix_a[row_ind_a, col_ind_a].sum()
        team_a_assignment = {team_a_participants[i]['이름']: positions[j] for i, j in zip(row_ind_a, col_ind_a)}

        cost_matrix_b = np.array([[-score_matrix[p['이름']][pos] for pos in positions] for p in team_b_participants])
        row_ind_b, col_ind_b = linear_sum_assignment(cost_matrix_b)
        team_b_score = -cost_matrix_b[row_ind_b, col_ind_b].sum()
        team_b_assignment = {team_b_participants[i]['이름']: positions[j] for i, j in zip(row_ind_b, col_ind_b)}
        
        score_diff = abs(team_a_score - team_b_score)

        if score_diff < min_score_diff:
            min_score_diff = score_diff
            best_combination = [(team_a_assignment, team_a_score, team_b_assignment, team_b_score)]
        elif score_diff == min_score_diff:
            best_combination.append((team_a_assignment, team_a_score, team_b_assignment, team_b_score))

    if not best_combination:
        return None, "최적의 팀을 찾지 못했습니다."
        
    final_team_a_assign, final_team_a_score, final_team_b_assign, final_team_b_score = random.choice(best_combination)
    
    result = {
        'team_a': {'score': final_team_a_score, 'players': {}},
        'team_b': {'score': final_team_b_score, 'players': {}},
    }
    for name, pos in final_team_a_assign.items():
        result['team_a']['players'][name] = {'position': pos, 'score': score_matrix[name][pos]}
    for name, pos in final_team_b_assign.items():
        result['team_b']['players'][name] = {'position': pos, 'score': score_matrix[name][pos]}
        
    return result, "성공"


# ----------------------------------------------------------------
# 디스코드 봇 이벤트 및 명령어
# ----------------------------------------------------------------

@app.event
async def on_ready():
    print(f'성공적으로 로그인되었습니다: {app.user}')
    game = discord.Game("$도움말")
    await app.change_presence(status=discord.Status.online, activity=game)

@app.command(aliases=['도움말'])
async def help(ctx):
    embed = discord.Embed(title="📜 팀 빌딩 봇 도움말", description="팀 구성을 위한 명령어 목록입니다.", color=0x5865F2)
    embed.add_field(name="$team [이름1] [이름2] ...", value="참가할 플레이어 10명의 이름을 입력하여 팀을 구성합니다.\n- 같이할 플레이어는 `+`로 묶어주세요 (예: `이름1+이름2`)\n- 이름에 띄어쓰기가 있다면 `\"따옴표\"`로 감싸주세요", inline=False)
    embed.add_field(name="$lol [닉네임#태그]", value="롤 티어를 검색합니다. (예: `$lol 페이커#KR1`)", inline=False)
    embed.set_footer(text="문의사항은 관리자에게 연락해주세요. https://github.com/sihyeok000/teambuilderBot")
    await ctx.send(embed=embed)

# ----------------------------------------------------------------
# ✨ 수정된 롤 전적 검색 명령어
# ----------------------------------------------------------------
@app.command(aliases=['l', '롤'])
async def lol(ctx, *, summoner_name: str):
    """League of Legends 소환사 정보를 검색하여 Embed로 표시합니다."""
    
    msg = await ctx.send(embed=discord.Embed(description=f"🔍 **{summoner_name}** 님의 정보를 검색하고 있습니다...", color=0x5865F2))

    try:
        # 1. 소환사 이름과 태그 분리
        gameName, tagLine = getNameTag(summoner_name)

        # 2. 계정 정보(PUUID) 가져오기
        account_response = get_PUUID(gameName, tagLine)
        # --- 디버깅용: API 응답 확인 ---
        # print("Account API Response:", account_response)
        
        if 'puuid' not in account_response:
            error_embed = discord.Embed(title="오류 발생", description=f"**'{summoner_name}'** 소환사를 찾을 수 없습니다.\n`이름#태그` 형식으로 정확히 입력했는지 확인해주세요.", color=0xE74C3C)
            await msg.edit(embed=error_embed)
            return

        # 3. 소환사 정보 가져오기
        puuid = account_response['puuid']
        summoner_info = get_summonerinfo_by_puuid(puuid)
        # --- 디버깅용: API 응답 확인 ---
        # print("Summoner API Response:", summoner_info)

        if 'id' not in summoner_info:
            error_embed = discord.Embed(title="오류 발생", description="Riot API에서 소환사 정보를 불러오는 데 실패했습니다.", color=0xE74C3C)
            await msg.edit(embed=error_embed)
            return

        summoner_id = summoner_info['id']
        summoner_level = summoner_info['summonerLevel']
        profile_icon_id = summoner_info['profileIconId']

        # 4. DDragon에서 최신 버전 정보 가져오기
        try:
            versions = requests.get("https://ddragon.leagueoflegends.com/api/versions.json").json()
            latest_version = versions[0]
        except Exception:
            latest_version = "14.15.1" # 실패 시 대체 버전

        icon_url = f"https://ddragon.leagueoflegends.com/cdn/{latest_version}/img/profileicon/{profile_icon_id}.png"

        # 5. 랭크 정보 가져오기
        rank_info_list = get_league_info_by_summoner_id(summoner_id)
        # --- 디버깅용: API 응답 확인 ---
        # print("League API Response:", rank_info_list)

        solo_rank_info = next((r for r in rank_info_list if r.get('queueType') == 'RANKED_SOLO_5x5'), None)
        flex_rank_info = next((r for r in rank_info_list if r.get('queueType') == 'RANKED_FLEX_SR'), None)

        # 6. 결과 Embed 생성
        embed_color = rank_color.get(solo_rank_info.get('tier') if solo_rank_info else (flex_rank_info.get('tier') if flex_rank_info else 'IRON'), 0x5865F2)
        
        embed = discord.Embed(title=f"{gameName}#{tagLine}", description=f"**레벨:** {summoner_level}", color=embed_color)
        embed.set_thumbnail(url=icon_url)

        # 솔로랭크 정보 필드
        if solo_rank_info:
            tier, rank, lp, wins, losses = solo_rank_info['tier'], solo_rank_info['rank'], solo_rank_info['leaguePoints'], solo_rank_info['wins'], solo_rank_info['losses']
            win_rate = round((wins / (wins + losses)) * 100) if (wins + losses) > 0 else 0
            embed.add_field(name="솔로랭크", value=f"**{tier} {rank}** ({lp} LP)\n{wins}승 {losses}패 ({win_rate}%)", inline=True)
        else:
            embed.add_field(name="솔로랭크", value="Unranked", inline=True)

        # 자유랭크 정보 필드
        if flex_rank_info:
            tier, rank, lp, wins, losses = flex_rank_info['tier'], flex_rank_info['rank'], flex_rank_info['leaguePoints'], flex_rank_info['wins'], flex_rank_info['losses']
            win_rate = round((wins / (wins + losses)) * 100) if (wins + losses) > 0 else 0
            embed.add_field(name="자유랭크", value=f"**{tier} {rank}** ({lp} LP)\n{wins}승 {losses}패 ({win_rate}%)", inline=True)
        else:
            embed.add_field(name="자유랭크", value="Unranked", inline=True)
        
        embed.set_footer(text="Powered by Riot Games API")
        await msg.edit(embed=embed)

    except Exception as e:
        print(f"[$lol 명령어 오류] {e}")
        error_embed = discord.Embed(title="알 수 없는 오류", description="명령어 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.", color=0xE74C3C)
        await msg.edit(embed=error_embed)


@app.command()
async def team(ctx, *player_inputs):
    grouped_players = []
    solo_players = []
    for p_input in player_inputs:
        if '+' in p_input:
            grouped_players.extend(p_input.split('+'))
        else:
            solo_players.append(p_input)
    
    total_players = len(grouped_players) + len(solo_players)
    if total_players != 10:
        await ctx.send(f"💥 팀을 구성하려면 10명의 플레이어 이름이 필요합니다! (현재 {total_players}명)")
        return
        
    if len(grouped_players) > 4:
        await ctx.send("💥 한 팀에 속할 그룹은 최대 4명까지 지정할 수 있습니다!")
        return

    msg = await ctx.send("🤔 최적의 팀 조합을 계산하고 있습니다. 잠시만 기다려주세요...")

    tier_scores, position_weights, player_db = get_lol_data_from_sheet()
    if not player_db:
        await msg.edit(content="😵 구글 시트에서 데이터를 가져오는 데 실패했습니다. 설정을 확인해주세요.")
        return

    result, message = balance_teams(grouped_players, solo_players, tier_scores, position_weights, player_db)

    if not result:
        await msg.edit(content=f"😥 팀 구성에 실패했습니다! 이유: {message}")
        return

    team_a = result['team_a']
    team_b = result['team_b']
    
    if any(p in team_b['players'] for p in grouped_players):
        blue_team, red_team = team_b, team_a
        blue_name, red_name = "B팀", "A팀"
    else:
        blue_team, red_team = team_a, team_b
        blue_name, red_name = "A팀", "B팀"

    embed = discord.Embed(title="⚔️ 팀 빌딩 결과 ⚔️", color=0x3498DB)
    
    position_order = ['탑', '정글', '미드', '원딜', '서폿']

    def create_team_text(team_data):
        players_by_pos = {data['position']: name for name, data in team_data['players'].items()}
        text = ""
        for pos in position_order:
            player_name = players_by_pos.get(pos)
            if player_name:
                player_data = team_data['players'][player_name]
                text += f"**{pos}**: {player_name} ({player_data['score']:.1f}점)\n"
        return text

    blue_team_text = create_team_text(blue_team)
    embed.add_field(name=f"🔵 {blue_name} (총점: {blue_team['score']:.1f})", value=blue_team_text, inline=True)

    red_team_text = create_team_text(red_team)
    embed.add_field(name=f"🔴 {red_name} (총점: {red_team['score']:.1f})", value=red_team_text, inline=True)
    
    score_diff = abs(blue_team['score'] - red_team['score'])
    embed.set_footer(text=f"두 팀의 점수 차이: {score_diff:.2f}점 | 최적의 밸런스로 팀이 구성되었습니다.")

    await msg.edit(content=None, embed=embed)

# 봇 실행
try:
    app.run(os.getenv('discord_key'))
except Exception as e:
    print(f"봇 실행 중 오류 발생: {e}")
