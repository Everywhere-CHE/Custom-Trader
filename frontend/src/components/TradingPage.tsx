/** 主交易页面：三栏布局（市场列表 | K 线图 | 交易面板）+ 底部标签页。
 *  仅视觉层：数据流与业务逻辑保持不变。高度由 App 的 main 容器撑满。 */

import BottomTabs from './BottomTabs'
import KlineChart from './KlineChart'
import MarketList from './MarketList'
import OrderPanel from './OrderPanel'
import GlassCard from './ui/GlassCard'

export default function TradingPage() {
  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex min-h-0 flex-1 gap-3">
        <GlassCard className="w-[264px] shrink-0 overflow-auto" noLift>
          <MarketList />
        </GlassCard>

        <GlassCard className="min-w-0 flex-1" noLift>
          <KlineChart />
        </GlassCard>

        <GlassCard className="w-[304px] shrink-0 overflow-auto" noLift>
          <OrderPanel />
        </GlassCard>
      </div>

      <GlassCard className="h-[248px] shrink-0 overflow-hidden" noLift>
        <BottomTabs />
      </GlassCard>
    </div>
  )
}
