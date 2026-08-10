type Props = {
  start: string;
  end: string;
  onChange: (next: { start: string; end: string }) => void;
};

export function WindowPicker({ start, end, onChange }: Props) {
  return (
    <div className="window-picker">
      <label>
        起始日期
        <input
          type="date"
          value={start}
          onChange={(event) => onChange({ start: event.target.value, end })}
        />
      </label>
      <label>
        截止日期
        <input
          type="date"
          value={end}
          onChange={(event) => onChange({ start, end: event.target.value })}
        />
      </label>
    </div>
  );
}
