---------------------------- MODULE Reservations ----------------------------
(* The reservation of the worst case of each model call, as frontier/budget.py does it.

   Promise: model calls made in parallel never spend more than the LLM cap together.

   Each action is one step that the code takes without another process in between:
     TakeLock       Budget._reservations: takes the reservations lock and reads reservations.json
     Decide         Budget.reserve / _fits: still under the lock, reads the ledger, then writes the reservation
                    or refuses, and gives the lock back
     Charge         Budget.charge_llm: one line appended to llm.jsonl (Call.charge, or the worst case on exit)
     Release        Budget.release: takes the lock, removes the reservation, gives the lock back

   A call that is void is a Charge of 0. Amounts are whole units; every call reserves Worst.

   Variant "code" is the code. The other two are mistakes the model must catch:
     "release_first"  the reservation is released before the charge is written (agent.py before e6eead1)
     "ledger_first"   the ledger is read before the lock is taken, so the spend it saw can be stale *)
EXTENDS Naturals, FiniteSets

CONSTANTS Calls, Cap, Worst, Variant, NoOne

VARIABLES spent,      \* the sum of the ledger
          res,        \* reservations.json: what each call holds
          pc,         \* where each call is
          lock,       \* who holds the reservations lock
          seenRes,    \* the sum of reservations a call read under the lock
          seenSpent   \* "ledger_first" only: the spend a call read before the lock

vars == <<spent, res, pc, lock, seenRes, seenSpent>>

RECURSIVE Sum(_, _)
Sum(f, S) == IF S = {} THEN 0 ELSE LET c == CHOOSE x \in S : TRUE IN f[c] + Sum(f, S \ {c})

Init == /\ spent = 0
        /\ res = [c \in Calls |-> 0]
        /\ pc = [c \in Calls |-> "start"]
        /\ lock = NoOne
        /\ seenRes = [c \in Calls |-> 0]
        /\ seenSpent = [c \in Calls |-> 0]

ReadLedgerEarly(c) ==
    /\ Variant = "ledger_first" /\ pc[c] = "start"
    /\ seenSpent' = [seenSpent EXCEPT ![c] = spent]
    /\ pc' = [pc EXCEPT ![c] = "read"]
    /\ UNCHANGED <<spent, res, lock, seenRes>>

TakeLock(c) ==
    /\ pc[c] = (IF Variant = "ledger_first" THEN "read" ELSE "start")
    /\ lock = NoOne
    /\ lock' = c
    /\ seenRes' = [seenRes EXCEPT ![c] = Sum(res, Calls)]
    /\ pc' = [pc EXCEPT ![c] = "locked"]
    /\ UNCHANGED <<spent, res, seenSpent>>

Decide(c) ==
    /\ pc[c] = "locked"
    /\ lock' = NoOne
    /\ LET s == IF Variant = "ledger_first" THEN seenSpent[c] ELSE spent
       IN IF s >= Cap \/ s + seenRes[c] + Worst > Cap
          THEN /\ pc' = [pc EXCEPT ![c] = "refused"]
               /\ UNCHANGED res
          ELSE /\ pc' = [pc EXCEPT ![c] = "running"]
               /\ res' = [res EXCEPT ![c] = Worst]
    /\ UNCHANGED <<spent, seenRes, seenSpent>>

Charge(c) ==
    /\ pc[c] = (IF Variant = "release_first" THEN "released" ELSE "running")
    /\ \E cost \in 0..Worst : spent' = spent + cost
    /\ pc' = [pc EXCEPT ![c] = IF Variant = "release_first" THEN "done" ELSE "charged"]
    /\ UNCHANGED <<res, lock, seenRes, seenSpent>>

Release(c) ==
    /\ pc[c] = (IF Variant = "release_first" THEN "running" ELSE "charged")
    /\ lock = NoOne
    /\ res' = [res EXCEPT ![c] = 0]
    /\ pc' = [pc EXCEPT ![c] = IF Variant = "release_first" THEN "released" ELSE "done"]
    /\ UNCHANGED <<spent, lock, seenRes, seenSpent>>

AllEnded == /\ \A c \in Calls : pc[c] \in {"done", "refused"}
            /\ UNCHANGED vars

Next == \/ \E c \in Calls : ReadLedgerEarly(c) \/ TakeLock(c) \/ Decide(c) \/ Charge(c) \/ Release(c)
        \/ AllEnded

Spec == Init /\ [][Next]_vars

\* The promise.
WithinCap == spent <= Cap

\* Why it holds: a call is counted at its worst case until its charge is written.
NotCharged(c) == pc[c] \in {"running", "released"}
Covered == spent + Sum([c \in Calls |-> IF NotCharged(c) THEN Worst ELSE 0], Calls) <= Cap

\* No reservation outlives its call.
NothingLeft == (\A c \in Calls : pc[c] \in {"done", "refused"}) => Sum(res, Calls) = 0
=============================================================================
