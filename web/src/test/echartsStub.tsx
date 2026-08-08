// test stand-in for echarts-for-react: jsdom has no canvas, and the pages'
// tests assert on the serialized option instead of pixels
export default function ReactECharts({
  option,
  style,
}: {
  option: unknown;
  style?: React.CSSProperties;
}) {
  return <div data-testid="echart" data-option={JSON.stringify(option)} style={style} />;
}
