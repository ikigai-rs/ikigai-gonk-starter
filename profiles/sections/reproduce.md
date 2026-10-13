## 3. Reproduce before you fix

A bug report, a review finding, or a measurement someone else made is a **claim**, not a
task. Reproduce it first, preferably as a test that fails because of the defect. If it does
not reproduce, report it as not a defect, with the evidence (the dispatcher closes it with
the reason that fits, such as `--reason wontfix`); closing with no change is a good
outcome. If it is partly true, fix the true part and say what you did not do and why.
Never make a change only because the item expected one.
