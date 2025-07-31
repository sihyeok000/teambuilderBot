import discord
from discord.ext import commands
import gspread
import itertools
import random
import numpy as np
from scipy.optimize import linear_sum_assignment
import os
from dotenv import load_dotenv

# .env 파일에서 환경 변수 로드
load_dotenv(dotenv_path="./.env")

# 봇 기본 설정
intents = discord.Intents.default()
intents.message_content = True
app = commands.Bot(command_prefix='$', help_command=None, intents=intents)


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
        settings_data = settings_sheet.get_all_records()
        
        tier_scores = {str(item['키']).strip(): item['값'] for item in settings_data if item['설정명'] == '티어점수'}
        position_weights = {str(item['키']).strip(): item['값'] for item in settings_data if item['설정명'] == '포지션가중치'}
        
        player_db_sheet = spreadsheet.worksheet("플레이어_DB")
        player_db = player_db_sheet.get_all_records()
        
        return tier_scores, position_weights, player_db
    except Exception as e:
        print(f"시트 데이터 로딩 오류: {e}")
        return None, None, None

def balance_teams(player_names, tier_scores, position_weights, player_db):
    """주어진 10명의 플레이어 이름으로 최적의 팀 밸런스를 맞춥니다."""
    
    positions = ['탑', '정글', '미드', '원딜', '서폿']
    
    participants = [p for p in player_db if p.get('이름') in player_names]
    
    if len(participants) != 10:
        found_names = {p['이름'] for p in participants}
        missing_names = set(player_names) - found_names
        return None, f"다음 플레이어를 DB에서 찾을 수 없습니다: {', '.join(missing_names)}"

    # ==================================================================
    # ✨ 디버깅 코드 시작
    # ==================================================================
    print("\n\n--- [디버깅] 환산점수 계산 과정 시작 ---")
    print(f"참가자 목록: {[p['이름'] for p in participants]}")
    print(f"찾은 티어 점수 설정: {tier_scores}")
    # ==================================================================

    score_matrix = {}
    for player in participants:
        player_name = player['이름']
        
        player_tier_str = str(player.get('티어', '')).strip()
        tier_score = tier_scores.get(player_tier_str, 0)
        
        # ==================================================================
        # ✨ 디버깅 코드
        # ==================================================================
        print(f"\n[플레이어: {player_name}]")
        print(f"  - 시트에서 읽은 티어: '{player_tier_str}'")
        print(f"  - 매칭된 티어 점수: {tier_score}")
        # ==================================================================

        score_matrix[player_name] = {}
        for pos in positions:
            proficiency = player.get(pos, 1)
            weight = position_weights.get(pos, 0.5)
            calculated_score = tier_score * (1 + (proficiency - 1) * weight)
            score_matrix[player_name][pos] = calculated_score
            
            # ==================================================================
            # ✨ 디버깅 코드
            # ==================================================================
            print(f"    -> {pos:2s} 숙련도({proficiency}) | 최종 점수: {calculated_score:.2f}")
            # ==================================================================

    # ==================================================================
    # ✨ 디버깅 코드 종료
    # ==================================================================
    print("--- [디버깅] 환산점수 계산 완료 ---\n\n")
    # ==================================================================

    best_combination = []
    min_score_diff = float('inf')
    
    player_indices = list(range(10))
    for team_a_indices in itertools.combinations(player_indices, 5):
        team_b_indices = list(set(player_indices) - set(team_a_indices))
        
        team_a_players = [participants[i] for i in team_a_indices]
        team_b_players = [participants[i] for i in team_b_indices]

        cost_matrix_a = np.array([[-score_matrix[p['이름']][pos] for pos in positions] for p in team_a_players])
        row_ind_a, col_ind_a = linear_sum_assignment(cost_matrix_a)
        team_a_score = -cost_matrix_a[row_ind_a, col_ind_a].sum()
        team_a_assignment = {team_a_players[i]['이름']: positions[j] for i, j in zip(row_ind_a, col_ind_a)}

        cost_matrix_b = np.array([[-score_matrix[p['이름']][pos] for pos in positions] for p in team_b_players])
        row_ind_b, col_ind_b = linear_sum_assignment(cost_matrix_b)
        team_b_score = -cost_matrix_b[row_ind_b, col_ind_b].sum()
        team_b_assignment = {team_b_players[i]['이름']: positions[j] for i, j in zip(row_ind_b, col_ind_b)}
        
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
    embed.add_field(name="$team [이름1] [이름2] ... [이름10]", value="참가할 플레이어 10명의 이름을 입력하여 팀을 구성합니다.\n(이름에 띄어쓰기가 있다면 \"따옴표\"로 감싸주세요)", inline=False)
    embed.set_footer(text="문의사항은 관리자에게 연락해주세요.")
    await ctx.send(embed=embed)


@app.command()
async def team(ctx, *player_names):
    if len(player_names) != 10:
        await ctx.send("� 팀을 구성하려면 10명의 플레이어 **이름**이 필요합니다! (예: `$team 이름1 이름2 ... 이름10`)")
        return

    await ctx.send("🤔 최적의 팀 조합을 계산하고 있습니다. 잠시만 기다려주세요...")

    tier_scores, position_weights, player_db = get_lol_data_from_sheet()
    if not player_db:
        await ctx.send("😵 구글 시트에서 데이터를 가져오는 데 실패했습니다. 설정을 확인해주세요.")
        return

    result, message = balance_teams(player_names, tier_scores, position_weights, player_db)

    if not result:
        await ctx.send(f"😥 팀 구성에 실패했습니다! 이유: {message}")
        return

    team_a = result['team_a']
    team_b = result['team_b']
    
    if team_a['score'] > team_b['score']:
        blue_team, red_team = team_a, team_b
        blue_name, red_name = "A팀", "B팀"
    else:
        blue_team, red_team = team_b, team_a
        blue_name, red_name = "B팀", "A팀"

    embed = discord.Embed(title="⚔️ 팀 빌딩 결과 ⚔️", color=0x3498DB)
    
    blue_team_text = ""
    for name, data in blue_team['players'].items():
        blue_team_text += f"**{data['position']}**: {name} ({data['score']:.1f}점)\n"
    embed.add_field(name=f"🔵 {blue_name} (총점: {blue_team['score']:.1f})", value=blue_team_text, inline=True)

    red_team_text = ""
    for name, data in red_team['players'].items():
        red_team_text += f"**{data['position']}**: {name} ({data['score']:.1f}점)\n"
    embed.add_field(name=f"🔴 {red_name} (총점: {red_team['score']:.1f})", value=red_team_text, inline=True)
    
    score_diff = abs(blue_team['score'] - red_team['score'])
    embed.set_footer(text=f"두 팀의 점수 차이: {score_diff:.2f}점 | 최적의 밸런스로 팀이 구성되었습니다.")

    await ctx.send(embed=embed)

# 봇 실행
try:
    app.run(os.getenv('discord_key'))
except Exception as e:
    print(f"봇 실행 중 오류 발생: {e}")