import { useId } from "react";
import CreatableSelect from "react-select/creatable";

export function TagInput({
  label,
  value,
  onChange,
  hint,
  invalid,
  id,
}: {
  label: string;
  value: string[];
  onChange: (v: string[]) => void;
  hint?: string;
  invalid?: boolean;
  id?: string;
}) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const hintId = hint ? `${inputId}-hint` : undefined;

  const selectValue = value.map((v) => ({ label: v, value: v }));

  return (
    <div>
      <label
        htmlFor={inputId}
        className="tile-label"
        style={{ display: "block", marginBottom: "var(--s-1-5)" }}
      >
        {label}
      </label>
      <CreatableSelect
        inputId={inputId}
        isMulti
        value={selectValue}
        onChange={(newValue: any) => {
          onChange((newValue || []).map((v: any) => v.value));
        }}
        styles={{
          control: (base, state) => ({
            ...base,
            backgroundColor: "var(--surface-2)",
            borderColor: invalid ? "var(--decline)" : state.isFocused ? "var(--accent)" : "var(--border)",
            boxShadow: state.isFocused ? `0 0 0 1px ${invalid ? "var(--decline)" : "var(--accent)"}` : "none",
            "&:hover": {
              borderColor: invalid ? "var(--decline)" : "var(--accent)",
            },
            color: "var(--text-primary)",
          }),
          menu: (base) => ({
            ...base,
            backgroundColor: "var(--surface-2)",
            border: "1px solid var(--border)",
          }),
          option: (base, state) => ({
            ...base,
            backgroundColor: state.isFocused ? "var(--surface-3)" : "transparent",
            color: "var(--text-primary)",
            cursor: "pointer",
            "&:active": {
              backgroundColor: "var(--surface-3)",
            },
          }),
          multiValue: (base) => ({
            ...base,
            backgroundColor: "var(--surface-3)",
            border: "1px solid var(--border)",
          }),
          multiValueLabel: (base) => ({
            ...base,
            color: "var(--text-primary)",
          }),
          multiValueRemove: (base) => ({
            ...base,
            color: "var(--text-secondary)",
            "&:hover": {
              backgroundColor: "var(--decline)",
              color: "#fff",
            },
          }),
          input: (base) => ({
            ...base,
            color: "var(--text-primary)",
          }),
        }}
      />
      {hint && (
        invalid ? (
          <div
            id={hintId}
            style={{
              marginTop: "var(--s-1-5)",
              fontSize: "var(--t-caption)",
              color: "var(--decline)",
            }}
          >
            {hint}
          </div>
        ) : (
          <details id={hintId} style={{ marginTop: "var(--s-1-5)", fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>
            <summary style={{ cursor: "pointer", userSelect: "none", color: "var(--text-secondary)", outline: "none" }}>More info</summary>
            <div style={{ marginTop: "var(--s-1)", lineHeight: 1.4 }}>{hint}</div>
          </details>
        )
      )}
    </div>
  );
}
