import ReactECharts from 'echarts-for-react'

const axis = {axisLine: {lineStyle: {color: '#34383d'}}, axisLabel: {color: '#747b83'}, splitLine: {lineStyle: {color: '#1c2023'}}}

export function EquityChart({paths, start = 0}: {paths: number[][]; start?: number}) {
  const series = paths.slice(0, 48).map((path, index) => ({
    type: 'line', data: path.map(v => v + start), showSymbol: false, silent: true,
    lineStyle: {width: index === 0 ? 1.5 : .6, opacity: index === 0 ? 1 : .28, color: index === 0 ? '#38e0ad' : index % 4 === 0 ? '#d96b66' : '#36a8b8'},
  }))
  return <ReactECharts style={{height: 310}} option={{animation: false, grid: {left: 56, right: 20, top: 20, bottom: 38}, tooltip: {trigger: 'axis'}, xAxis: {type: 'category', ...axis}, yAxis: {type: 'value', ...axis}, series}} />
}

export function RegimeChart({items}: {items: {name: string; net_pnl: number}[]}) {
  return <ReactECharts style={{height: 240}} option={{animation: false, grid: {left: 48, right: 10, top: 14, bottom: 40}, xAxis: {type: 'category', data: items.map(x => x.name), ...axis}, yAxis: {type: 'value', ...axis}, series: [{type: 'bar', data: items.map(x => ({value: x.net_pnl, itemStyle: {color: x.net_pnl >= 0 ? '#38e0ad' : '#e4615c'}})), barWidth: '48%'}]}} />
}
