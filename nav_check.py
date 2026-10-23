with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Remove lines 91-94 (0-indexed: 90-93) which are the old duplicate dashboard route
# Line 91: @app.route("/")
# Line 92: def dashboard():
# Line 93:     """Serves the main monitoring dashboard."""
# Line 94:     return render_template("dashboard.html")
# Line 95: (blank)

# Find the old duplicate by scanning for the pattern
new_lines = []
i = 0
removed = False
while i < len(lines):
    # Skip the OLD dashboard route block (first occurrence, around line 90-94)
    if (not removed and i < 100 and
        lines[i].strip() == '@app.route("/")' and
        i+1 < len(lines) and lines[i+1].strip() == 'def dashboard():'):
        # Skip this block (5 lines)
        print(f"Removing duplicate at line {i+1}: {repr(lines[i].strip())}")
        i += 5  # skip route + def + docstring + return + blank
        removed = True
        continue
    new_lines.append(lines[i])
    i += 1

print(f"Removed: {removed}, Total lines: {len(lines)} -> {len(new_lines)}")
with open("app.py", "w", encoding="utf-8") as f:
    f.writelines(new_lines)
print("Done!")
