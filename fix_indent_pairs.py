with open("scripts/refresh_validations.py", "r") as f:
    lines = f.readlines()

out = []
for i, line in enumerate(lines):
    if line == '    valid_idx = signals_df.dropna(subset=["spread", "z_score", "beta", "position"]).index\n':
        if "else:" in lines[i-2] or "else:" in lines[i-1]:
            out.append('        valid_idx = signals_df.dropna(subset=["spread", "z_score", "beta", "position"]).index\n')
        else:
            out.append('    valid_idx = signals_df.dropna(subset=["spread", "z_score", "beta", "position"]).index\n')
    else:
        out.append(line)

with open("scripts/refresh_validations.py", "w") as f:
    f.writelines(out)
