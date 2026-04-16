import logging
import random
import time

from wqb import FilterRange

from ex_sa import *


# 在 ex_re_check_green.py 中添加辅助函数
s = wqbs


def set_alpha_properties(
        s,
        alpha_id,
        name: str = None,
        color: str = None,
        selection_desc: str = None,
        combo_desc: str = None,
        tags: list = [],
        category: str = None,
        params={},
        # regula
):
    """
    Function changes alpha's description parameters
    """

    # params = {
    # }
    if name:
        params["name"] = name
    if color:
        params["color"] = color
    if selection_desc:
        params["selection_desc"] = selection_desc
    if combo_desc:
        params["combo_desc"] = combo_desc
    if tags:
        params["tags"] = tags
    if category:
        params["category"] = category
    response = s.patch(
        "https://api.worldquantbrain.com/alphas/" + alpha_id, json=params
    )
def set_alphas_properties(
        s,
        params: list = [],
):
    if not params:
        return
    response = s.patch(
        "https://api.worldquantbrain.com/alphas", json=params
    )
    return response

def get_submit_alphas(_date_start='2025-07-01', _date_end='2099-07-10', region='EUR'):

    lo = datetime.fromisoformat(f'{_date_start}T00:00:00-04:00')
    hi = datetime.fromisoformat(f'{_date_end}T00:00:00-04:00')
    alpha_ids = []
    max_tries = 5
    ty_count = 0
    while ty_count < max_tries:
        try:
            resps = wqbs.filter_alphas(
                status='ACTIVE',
                region=region,  # 'EUR', #'GLB' 'USA'，'ASI'
                delay=1,
                # # universe='TOP3000',
                # sharpe=FilterRange.from_str(_sharpe),
                # fitness=FilterRange.from_str('[1, inf)'),
                date_submitted=FilterRange.from_str(f"[{lo.isoformat()}, {hi.isoformat()})"),
                # date_created=FilterRange.from_str(f"[{lo.isoformat()}, {hi.isoformat()})"),
                # color=color,  # 'RED', 'YELLOW', 'GREEN', 'BLUE', 'PURPLE'
                # order='dateCreated',
                # name=name,
                # name='REGULAR',
                # order='-is.fitness',
                # order='-dateCreated',
                type='REGULAR',
                # tag = 'PowerPoolSelected',
                # prod_correlation=FilterRange.from_str('(0.7, inf)')

            )

            for resp in resps:
                res_data = resp.json()['results']
                for i in res_data:
                    alpha_ids.append(i['id'])
            break
        except Exception as e:
            print(e)
            ty_count += 1
            if ty_count >= max_tries:
                return None
            time.sleep(60)
    return alpha_ids


def select_alpha_set_category(wqbs, region, category):
    """
    选择指定区域和分类的alpha_id
    """
    region_list = ['EUR', 'GLB', 'USA', 'ASI', 'IND', 'KOR']

    for region in region_list:
        alpha_ids = []
        if region == 'USA':
            alpha_ids_user = []
            lo = datetime.fromisoformat(f'2025-07-05T00:00:00-04:00')
            hi = datetime.fromisoformat(f'2028-07-05T00:00:00-04:00')

            resps = wqbs.filter_alphas(
                status='ACTIVE',
                region=region,  # 'EUR', #'GLB' 'USA'，'ASI'
                delay=1,
                date_submitted=FilterRange.from_str(f"[{lo.isoformat()}, {hi.isoformat()})"),
                type='REGULAR',
                # # universe='TOP3000',
                # sharpe=FilterRange.from_str(_sharpe),
                # fitness=FilterRange.from_str('[1, inf)'),
                # category=category,
            )
            for resp in resps:
                alpha_ids.append(resp.id)
        return alpha_ids


def group_alpha_ids(alpha_ids, select_num, group_size=5):
    """
    将alpha_ids随机抽取select_num个，分成group_size个组
    """
    alpha_ids = random.sample(alpha_ids, int(select_num))
    alpha_ids_group = [alpha_ids[i:i + group_size] for i in range(0, len(alpha_ids), group_size)]


def cal_need_alpha_num(submit_num, ratio=1.5):
    if submit_num >= 100:
        ratio = 2
    else:
        ratio = 1.5
    select_num = submit_num // ratio
    # 判断select_num能否整除五，不能的话+1，直到满足要求
    while select_num % 5 != 0:
        select_num += 1
    # SUPER Alpha至少需要10个component alphas
    select_num = max(select_num, 10)
    return int(select_num)
def reset_alpha_name_usa(wqbs, region='USA',user_start='2025-03-01', user_end='2025-07-01',consultant_start='2025-07-01', consultant_end='2026-07-01'):
    alpha_ids_user = get_submit_alphas(_date_start=user_start, _date_end=user_end, region=region)
    if not alpha_ids_user:
        return None,None
    random.shuffle(alpha_ids_user)
    time.sleep(30)
    alpha_ids = get_submit_alphas(_date_start=consultant_start, _date_end=consultant_end, region=region)
    if not alpha_ids:
        return None,None
    if len(alpha_ids) + len(alpha_ids_user) < 10:
        print(f"区域 {region} 总共只有 {len(alpha_ids) + len(alpha_ids_user)} 个alpha，不足10个，跳过SUPER alpha创建。")
        return None, None
    select_num = cal_need_alpha_num(len(alpha_ids))
    print(select_num)
    alpha_ids_user = alpha_ids_user[:select_num//10]
    random.shuffle(alpha_ids)
    alpha_ids = alpha_ids[:select_num]
    # 把alpha_ids_user替换alpha_ids的前select_num//10个
    alpha_ids[:select_num//10] = alpha_ids_user
    random.shuffle(alpha_ids)
    group_num = 5
    group_size = int(select_num//group_num)
    #分成五组
    alpha_ids_group = [alpha_ids[i:i + group_size] for i in range(0, select_num, group_size)]
    # print(alpha_ids_group)
    # 根据当前时间生成五个组名
    now = datetime.now()
    group_names = [f'group_{now.strftime("%Y%m%d%H%M%S")}_{i}' for i in range(group_num)]
    print(group_names)
    # params = [{'id': 'MPjR6q5L', 'name': group_names[0]},
    #           {'id': 'vRK8KAOG', 'name': group_names[1]}]
    for index, alpha_ids in enumerate(alpha_ids_group):
        params = [{'id': alpha_id, 'name': group_names[index]} for alpha_id in alpha_ids]
        res = set_alphas_properties(wqbs, params)
        # print(res.json())
    return select_num,group_names


def reset_alpha_name_non_usa(wqbs, region='EUR',consultant_start='2025-07-05', consultant_end='2026-07-01'):
    alpha_ids = get_submit_alphas(_date_start=consultant_start, _date_end=consultant_end, region=region)
    if not alpha_ids:
        return None, None
    if len(alpha_ids) < 10:
        print(f"区域 {region} 只有 {len(alpha_ids)} 个已提交alpha，不足10个，跳过SUPER alpha创建。")
        return None, None
    select_num = cal_need_alpha_num(len(alpha_ids))
    print(select_num)
    random.shuffle(alpha_ids)
    group_num = 5
    group_size = int(select_num//group_num)
    #分成五组
    alpha_ids_group = [alpha_ids[i:i + group_size] for i in range(0, select_num, group_size)]
    # print(alpha_ids_group)
    # 根据当前时间生成五个组名
    now = datetime.now()
    group_names = [f'group_{now.strftime("%Y%m%d%H%M%S")}_{i}' for i in range(group_num)]
    print(group_names)
    # params = [{'id': 'MPjR6q5L', 'name': group_names[0]},
    #           {'id': 'vRK8KAOG', 'name': group_names[1]}]
    for index, alpha_ids in enumerate(alpha_ids_group):
        params = [{'id': alpha_id, 'name': group_names[index]} for alpha_id in alpha_ids]
        res = set_alphas_properties(wqbs, params)
        # print(res.json())
    return select_num,group_names
def gen_exp(group_names):
    sim_list = []
    for group_name in group_names:
        sim_list.append(f"""name == '{group_name}'""")
    exp = " || ".join(sim_list)
    return exp
norm_opt = ["INDUSTRY", "SUBINDUSTRY", "MARKET", "SECTOR"]
risk_opt = ["FAST", "SLOW", "SLOW_AND_FAST"]
r1 = ['STATISTICAL']
cr = ["CROWDING"]
co = ["COUNTRY"]
no = ["NONE"]
neut_opt = {"USA": norm_opt + cr + risk_opt + r1,
            "GLB": co + r1,
            "EUR": co + cr + norm_opt + risk_opt + r1,
            "ASI": co + cr + norm_opt + risk_opt + no,
            "CHN": norm_opt + cr + risk_opt + r1,
            "KOR": norm_opt,
            "TWN": norm_opt,
            "HKG": norm_opt,
            "JPN": norm_opt,
            "IND": co + cr + norm_opt + risk_opt,
            "AMR": ["COUNTRY"] + norm_opt,
            }
combo_exp = [

    '1',
    "combo_a(alpha)",

    'stata = generate_stats(alpha);\r\na = stata.pnl;\r\nts_mean(a, 20)/ts_std_dev(a, 20)\r\n',

    'stata = generate_stats(alpha);\r\na = stata.pnl;\r\nts_mean(a, 40)/ts_std_dev(a, 40)\r\n',

    'stata = generate_stats(alpha);\r\na = stata.pnl;\r\nts_mean(a, 250)/ts_std_dev(a, 250)\r\n',

    'stata = generate_stats(alpha);\r\na = stata.returns;\r\nts_mean(a, 20)/ts_std_dev(a, 20)\r\n',

    'stata = generate_stats(alpha);\r\na = stata.returns;\r\nts_mean(a, 40)/ts_std_dev(a, 40)\r\n',

    'stata = generate_stats(alpha);\r\na = stata.returns;\r\nts_mean(a, 250)/ts_std_dev(a, 250)\r\n',

    'stats = generate_stats(alpha); innerCorr = self_corr(stats.returns, 500); ic = if_else(innerCorr == 1.0, nan, innerCorr); maxCorr = reduce_max(ic); 1 - maxCorr',
    'stat=generate_stats(alpha); 1/ts_std_dev(stat.returns,252)'
    "stat=generate_stats(alpha); scale(1/ts_std_dev(stat.returns,252))+scale(combo_a(alpha, nlength = 250, mode = 'algo1'))",
    "stat=generate_stats(alpha); scale(1/ts_std_dev(stat.returns,252))+scale(combo_a(alpha))",
    """
    w1 = combo_a(alpha, nlength = 252, mode = 'algo1');
    w2 = combo_a(alpha, nlength = 252, mode = 'algo2');
    w3 = combo_a(alpha, nlength = 252, mode = 'algo3');
    scale(w1) + scale(w2) + scale(w3)
    """,
    """
    stats=generate_stats(alpha);
    portfolio_return = ts_sum(stats.returns,252);
    portfolio_stddev = ts_std_dev(stats.returns,252);
    portfolio_return / portfolio_stddev
    """,
    "combo_a(alpha, nlength = 250, mode = 'algo1')",
    f'stats = generate_stats(alpha); innerCorr = self_corr(stats.returns,250); ic = if_else(innerCorr == 1.0, nan, innerCorr); maxCorr = reduce_max(ic); 1 - maxCorr',

    f'stats = generate_stats(alpha);alpha_mom = ts_sum(stats.returns,60);mom_rank = ts_rank(alpha_mom,500);if_else(mom_rank>0.9,1,if_else(mom_rank<0.1,-1,0))',

    f'stats = generate_stats(alpha);std = ts_std_dev(stats.returns,60);std_crowd = std /ts_delay(std,60);ts_rank(-std_crowd ,500)',

    f'stats = generate_stats(alpha);tv = ts_mean(stats.trade_value,60);tv_crowd = tv/ts_delay(tv,60);ts_rank(-tv_crowd,500)',

    f'stats = generate_stats(alpha);ic = self_corr(stats.returns,60);inneric = if_else(ic==1,nan,ic);ts_rank(-reduce_min(inneric),500)',

    f'combo_a(alpha, nlength = 255, mode = "algo1")',

    f'combo_a(normalize(alpha), nlength = 250, mode = "algo1")'
    # 'stats = generate_stats(alpha); ts_ir(stats.pnl, 20) ',

    # 'stats = generate_stats(alpha); ts_ir(stats.pnl, 250) ',

    # 'stats = generate_stats(alpha); ts_ir(stats.returns, 250)',

    # 'stats = generate_stats(alpha); ts_ir(stats.turnover, 20) ',

    # 'stats = generate_stats(alpha); ts_ir(stats.turnover, 250)',

    # 'stats = generate_stats(alpha); ts_ir(stats.turnover, 3) '

]


def gen_super_list(region, select_num, group_names):
    exp = gen_exp(group_names)
    selection_exp = [
        f"""(own)&&({exp})"""
    ]
    # selection = f"""(own)&&({exp})"""
    sim_data_list = []
    print("\n开始创建 SUPER Alphas...")
    for neut in neut_opt[region]:
        for i in selection_exp:
            for _combo in combo_exp:
                # --- 阶段二：创建和重命名SUPER Alphas ---
                item_data = {
                    "type": "SUPER",
                    "settings": {
                        "nanHandling": "OFF",
                        "instrumentType": "EQUITY",
                        "delay": 1,
                        "universe": REGIONS.get(region, "TOP3000"),  # 使用.get()更安全
                        "truncation": 0.08,
                        "unitHandling": "VERIFY",
                        "selectionLimit": select_num,
                        "selectionHandling": "POSITIVE",
                        "pasteurization": "ON",
                        "region": region,
                        "language": "FASTEXPR",
                        "decay": 5,
                        "neutralization": neut,
                        "visualization": False,
                        'testPeriod': 'P0Y',
                        'maxTrade': 'ON',
                    },
                    "combo": _combo, "selection": i,
                }
                if region in ["ASI"]:
                    item_data["settings"]["maxTrade"] = "ON"
                sim_data_list.append(item_data)

    if not sim_data_list:
        print("\n没有需要创建的 SUPER alpha。程序结束。")
    else:
        # 使用线程池并行处理
        MAX_WORKERS = 3
        import random

        # 随机抽取50个元素组成新列表
        # 如果all_consultant_alphas中的元素少于100个，则抽取全部元素
        sample_size = min(200, len(sim_data_list))
        sim_data_list = random.sample(sim_data_list, sample_size)
        print(f"\n准备使用 {MAX_WORKERS} 个线程并行创建和重命名 {len(sim_data_list)} 个 SUPER alpha...")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # 提交所有任务
            futures = [executor.submit(simulate_and_rename_super_alpha, s, batch) for batch in sim_data_list[:]]
            # 等待所有任务完成
            wait(futures)

        print("\n所有滚动检测并生成SA处理任务已完成。")
if __name__ == '__main__':
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    # region_list = ['USA','EUR', 'GLB', 'ASI', 'IND']
    region_list = ['IND']
    user_start = '2025-03-01'
    user_end = '2025-07-05'
    consultant_start = '2025-07-05'
    consultant_end = '2066-07-05'

    while True:
        try:
            for region in region_list:
                if region == 'USA':
                    select_num,group_names = reset_alpha_name_usa(wqbs, region=region,user_start=user_start, user_end=user_end,consultant_start=consultant_start, consultant_end=consultant_end)

                else:
                    select_num,group_names = reset_alpha_name_non_usa(wqbs, region=region,consultant_start=consultant_start, consultant_end=consultant_end)
                if not select_num:
                    continue
                print(f"开始处理：region: {region}, select_num: {select_num}, group_names: {group_names}")
                gen_super_list(region,select_num,group_names)
        except Exception as e:
            print(f"发生错误：{e}")
            time.sleep(60*10)



