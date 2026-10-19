# Code smells

Baseline for the Standards axis. It applies even when the repo documents no standards, and it follows two rules.

- Smells are judgement calls. Label each as a possibility ("possible Feature Envy"), never as a hard violation. They are never a Blocking or Required finding, and the ceiling is Suggestions.
- A repo standard overrides this list: when a documented repo standard endorses what a smell would flag, suppress the smell. Skip anything the repo's tooling already enforces.

Match each smell against the changed lines. Each bullet reads what it is -> how to fix it.

## Smells

- **Mysterious Name**: a function, variable or type whose name does not say what it does or holds. -> Rename it; if no honest name comes, the design is unclear. (`smell`)
- **Duplicated Code**: the same logic shape appears in more than one hunk or file of the change. -> Extract the shared shape and call it from both places. (`smell`)
- **Feature Envy**: a method that reads another object's data more than its own. -> Move the method to the object that holds the data. (`smell`)
- **Data Clumps**: the same few fields or parameters travel together through several signatures. -> Bundle them into one type and pass that. (`smell`)
- **Primitive Obsession**: a bare number or string stands for a domain concept, such as money or an identifier. -> Give the concept a small type of its own. (`smell`)
- **Repeated Switches**: the same switch or if-chain on one kind of value recurs across the change. -> Replace it with polymorphism, or with one lookup that both sites share. (`smell`)
- **Shotgun Surgery**: one logical change forces small edits in many files. -> Gather what changes together into one module. (`smell`)
- **Divergent Change**: one module is edited for several unrelated reasons. -> Split it so each module changes for one reason. (`smell`)
- **Speculative Generality**: abstractions, parameters or hooks added for needs nobody has stated. -> Remove them, and inline back until a real need appears. (`smell`)
- **Message Chains**: a long `a.b().c().d()` walk that the caller should not depend on. -> Hide the walk behind one method on the first object. (`smell`)
- **Middle Man**: a class or function that mostly forwards calls to another. -> Remove it and call the real target. (`smell`)
- **Refused Bequest**: a subclass that ignores or overrides most of what it inherits. -> Drop the inheritance and use composition. (`smell`)

## Sources

- Martin Fowler, Refactoring, 2nd ed., chapter 3 "Bad Smells in Code" (names of the twelve smells), Addison-Wesley, 2018. Explanations are in this project's words.
- Smell selection and the override rule follow docs/matt-contract.md rows L38-L56 (matt.md, MIT; see NOTICE).
