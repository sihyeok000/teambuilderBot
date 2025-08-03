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
    splitted_name = summonerName.split('#')
    if len(splitted_name) == 2:
        gameName, tagLine = splitted_name
    else:
        gameName = summonerName
        tagLine = "KR1"

    return gameName, tagLine

def get_PUUID(gameName, tagLine):
    gameName = parse.quote(gameName)
    tagLine = parse.quote(tagLine)
    
    url = "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{}/{}".format(gameName, tagLine)
    return requests.get(url, headers=request_header).json()

def get_summonerinfo_by_puuid(puuid):
    url = "https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/"+puuid
    return requests.get(url, headers=request_header).json()


def league_v4_summoner_league(summoner_id):
    url = "https://kr.api.riotgames.com/lol/league/v4/entries/by-summoner/"+summoner_id
    return requests.get(url, headers=request_header).json()

def queueTypeCheck(queueType):
    if queueType=="RANKED_FLEX_SR":
        return "자유랭크"
    elif queueType=="RANKED_SOLO_5x5":
        return "솔로랭크"
    else:
        return queueType

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

#########################


# ----------------------------------------------------------------
# 팀 빌딩 핵심 로직
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
    
    # ✨ 수정된 부분: 조합 로직 변경
    # 그룹을 채워 5명으로 만들 나머지 인원 수 계산
    needed_for_group = 5 - len(grouped_players)
    
    # 솔로 플레이어 중에서 나머지 인원을 뽑는 모든 조합 생성
    for extra_players_tuple in itertools.combinations(solo_players, needed_for_group):
        extra_players = list(extra_players_tuple)
        
        # A팀, B팀 멤버 확정
        team_a_names = grouped_players + extra_players
        team_b_names = list(set(solo_players) - set(extra_players))
        
        team_a_participants = [p for p in participants if p['이름'] in team_a_names]
        team_b_participants = [p for p in participants if p['이름'] in team_b_names]

        # 이후 점수 계산 로직은 동일
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
    embed.add_field(name="$lol [닉네임#태그]", value="롤 티어를 검색합니다.", inline=False)
    embed.set_footer(text="문의사항은 관리자에게 연락해주세요. https://github.com/sihyeok000/teambuilderBot")
    await ctx.send(embed=embed)

@app.command(aliases = ['l','롤'])
async def lol(ctx, arg):
    embed=discord.Embed(title="League of Legends 전적검색[KR]", color=0x000000)
    error_occured = False
    
    #### Search Riot Id ####
    try:
        gameName, tagLine = getNameTag(arg)
        puuid = get_PUUID(gameName, tagLine).get('puuid')

        summoner_info = get_summonerinfo_by_puuid(puuid)

        summoner_id = summoner_info.get('id')
        prev_name = summoner_info.get('name')
        summonerLevel = summoner_info.get('summonerLevel')
        profileIconId = summoner_info.get('profileIconId')
    
    except:
        embed.add_field(name = arg, value="소환사 이름이 없습니다. 띄어쓰기 없이 [Name#Tag]와 같이 입력해주세요.", inline=False)
        error_occured = True
    
    ### Error occurred while searching Riot ID.
    if not error_occured:
        #### Load Rank info ####
        try:
            summoner_rank = league_v4_summoner_league(summoner_id)
            tier = summoner_rank[0].get('tier')
            rank = summoner_rank[0].get('rank')
            wins = summoner_rank[0].get('wins')
            losses = summoner_rank[0].get('losses')
            leaguePoints = summoner_rank[0].get('leaguePoints')
            queueType = summoner_rank[0].get('queueType')
            queueType = queueTypeCheck(queueType)
            
            embed.color = rank_color[tier]
            embed.add_field(name="{}#{} (prev.{}) Lv.{}".format(gameName, tagLine, prev_name, summonerLevel),
                    value="{} {} {} {}P\n{}승 {}패".format(queueType, tier, rank, leaguePoints, wins, losses),
                    inline=False)
            
        except:
            embed.add_field(name = "{}#{} (prev.{}) Lv.{}".format(gameName, tagLine, prev_name, summonerLevel), 
                            value="unranked", inline=False)
        
        
        #### Thumbnail Setting ####
        
        icon_url = "https://ddragon.leagueoflegends.com/cdn/10.18.1/img/profileicon/{}.png".format(profileIconId)
        
        try:
            response = requests.get(icon_url)
            response.raise_for_status()
            embed.set_thumbnail(url = icon_url)
        
        except:
            icon_url = "https://ddragon.leagueoflegends.com/cdn/10.18.1/img/profileicon/6.png"
            embed.set_thumbnail(url = icon_url)
        
    
    #### Result ####
    await ctx.send(embed=embed)
    
    
@app.command()
async def team(ctx, *player_inputs):
    # ✨ 수정된 부분: 명령어 파싱 로직 추가
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

    await ctx.send("🤔 최적의 팀 조합을 계산하고 있습니다. 잠시만 기다려주세요...")

    tier_scores, position_weights, player_db = get_lol_data_from_sheet()
    if not player_db:
        await ctx.send("😵 구글 시트에서 데이터를 가져오는 데 실패했습니다. 설정을 확인해주세요.")
        return

    # balance_teams 함수에 그룹/솔로 플레이어 명단을 전달
    result, message = balance_teams(grouped_players, solo_players, tier_scores, position_weights, player_db)

    if not result:
        await ctx.send(f"😥 팀 구성에 실패했습니다! 이유: {message}")
        return

    team_a = result['team_a']
    team_b = result['team_b']
    
    # 그룹 멤버가 항상 A팀에 오도록 보장
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

    await ctx.send(embed=embed)

# 봇 실행
try:
    app.run(os.getenv('discord_key'))
except Exception as e:
    print(f"봇 실행 중 오류 발생: {e}")