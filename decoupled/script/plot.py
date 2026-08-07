import matplotlib.pyplot as plt
import numpy as np

# 配置中文字体支持
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun', 'Arial Unicode MS', 'Arial'] 
plt.rcParams['axes.unicode_minus'] = False

nodes = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) # 对应 1, f+1, 2f+1, n
node_labels = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10']

busy_time = [200, 201, 201, 201, 202, 202, 202, 203, 203, 204]
idle_time = [200, 201, 202, 202, 202, 202, 202, 202, 203, 204]
total_time = np.array(busy_time) + np.array(idle_time)

# 下半部分数据 (散点)
dots_x = [
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 
    2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 
    3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 
    4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 
    5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 
    6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 
    8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 
    9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 
    10, 10, 10, 10, 10, 10, 10, 10, 10, 10
]
dots_y = [
    31, 55, 98, 101, 101, 199, 200, 200, 203, 203,
    32, 56, 100, 102, 103, 200, 201, 202, 203, 204,
    31, 56, 101, 102, 102, 200, 201, 202, 204, 204,
    29, 54, 98, 99, 100, 199, 201, 201, 202, 203,
    31, 56, 100, 101, 101, 200, 202, 202, 203, 203,
    33, 56, 102, 102, 103, 200, 202, 202, 203, 203,
    32, 55, 101, 102, 103, 201, 202, 202, 203, 203,
    30, 57, 101, 102, 102, 202, 202, 203, 205, 205,
    31, 56, 100, 101, 102, 202, 202, 203, 204, 204, 
    32, 57, 100, 101, 103, 203, 203, 204, 204, 205 
]

# --- 开始绘图 ---
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 8), sharex=True, 
                               gridspec_kw={'height_ratios': [1, 1]})
plt.subplots_adjust(hspace=0) # 消除两个子图之间的间隙

# --- 1. 绘制上半部分 (Bar Chart) ---
# 使用分组柱状图，让 idle time 在左边，busy time 在右边
width = 0.15
x_idle = nodes - width/2
x_busy = nodes + width/2

ax1.bar(x_idle, idle_time, width=width, label='idle time', color='#a8d0a4', edgecolor='gray')
ax1.bar(x_busy, busy_time, width=width, label='busy time', color='#f5b78d', edgecolor='gray')

# 绘制趋势线
# ax1.plot(nodes, total_time, color='blue', linewidth=1.5)
# ax1.text(nodes[-1] + 0.1, total_time[-1], 'straggler', va='center')

# # 绘制 w/o 参考线
# ax1.axhline(y=5.5, color='black', linewidth=1.5, xmin=0.1, xmax=0.9)
# ax1.text(nodes[-1] + 0.1, 5.5, 'w/o', va='center')

ax1.set_ylabel('time', loc='top', rotation=0, labelpad=-20)
ax1.legend(loc='upper right', frameon=False)

# --- 2. 绘制下半部分 (Scatter Plot) ---
ax2.scatter(dots_x, dots_y, color='black', s=30)

# 绘制连接线 (示例线)
line1_y = [101, 102, 102, 99, 101, 102, 102, 102, 101, 101] # 等到 f+1 的时间
line2_y = [200, 201, 201, 201, 202, 202, 202, 203, 203, 204] # 等到 2f+1 的时间


ax2.plot(nodes, line1_y, color='#7294d4', alpha=0.7)
ax2.text(nodes[-1] + 0.1, line1_y[-1], '等到f+1的时间', va='center')

ax2.plot(nodes, line2_y, color='#7294d4', alpha=0.7)
ax2.text(nodes[-1] + 0.1, line2_y[-1], '等到2f+1的时间', va='center')

# 图例说明
ax2.scatter([], [], color='black', label='顶点到达时间点')
ax2.text(1.2, 10, '● 顶点到达时间点', va='center')

ax2.set_ylabel('time', loc='bottom', rotation=0, labelpad=-20)
ax2.invert_yaxis() # 反转Y轴，让时间向下增长

# --- 3. 共有样式调整 ---
# 设置 X 轴标签
ax2.set_xticks(nodes)
ax2.set_xticklabels(node_labels)
ax2.set_xlabel('Node ID', loc='right')

# 隐藏坐标轴线 (类似原图的 L 型坐标系)
for ax in [ax1, ax2]:
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    # 移除刻度
    ax.tick_params(axis='both', which='both', length=0)

# 添加箭头效果
ax1.annotate('', xy=(0.8, 12), xytext=(0.8, 0), arrowprops=dict(arrowstyle="->", color='black'))
ax2.annotate('', xy=(0.8, 11), xytext=(0.8, 0), arrowprops=dict(arrowstyle="->", color='black'))
ax1.annotate('', xy=(4.5, 0), xytext=(0.8, 0), arrowprops=dict(arrowstyle="->", color='black'))

plt.show()