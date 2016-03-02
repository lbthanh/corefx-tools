import lldb
import shlex
import argparse
import os
import threading
import string

class DbgEngine(threading.local):

    def get_current_stack(self):
        thread = self.target.GetProcess().GetSelectedThread()
        return [DbgFrame(f) for f in thread.frames]
    
g_dbg = DbgEngine()

def __lldb_init_module(debugger, internal_dict):    
    debugger.HandleCommand('command script add -f triage.analyze analyze')
    debugger.HandleCommand('command script add -f triage.btm btm')

def init_debugger(debugger):
    g_dbg.debugger = debugger
    g_dbg.interpreter = debugger.GetCommandInterpreter()
    g_dbg.target = debugger.GetSelectedTarget()


def analyze(debugger, command, result, internal_dict):
    argList = shlex.split(command)

    dictArgs = { }
    for i, arg in enumerate(argList):
        key = arg
        if key.startswith('-'):
            val = ''
            if i < len(argList) and not argList[i+1].startswith('-'):
                val = argList[i+1]
            dictArgs[key] = val

    bAsync = debugger.GetAsync()
    debugger.SetAsync(False)

    init_debugger(debugger)
    
    eng = AnalysisEngine(dictArgs)
    
    eng.add_analyzer(StackTriageAnalyzer())
    
    dictProps = { }

    eng.analyze(dictProps);
        
    
    if '-o' in dictArgs:
        with open(dictArgs['-o'], 'w') as f:
            for key in dictProps.keys():
                f.write(key + ":\n")
                f.write(dictProps[key])
                f.write("\n\n")

    for key in dictProps.keys():
        result.AppendMessage(" ")
        result.AppendMessage(key + ":")
        result.AppendMessage(dictProps[key])

    debugger.SetAsync(bAsync)


def btm(debugger, command, result, internal_dict):
    bAsync = debugger.GetAsync()
    debugger.SetAsync(False)
    
    init_debugger(debugger)
    
    lstFrame = g_dbg.get_current_stack()

    for i, frame in enumerate(lstFrame):
        print str(i) + "\t" + frame.strIp + " " + str(frame)
        result.AppendMessage(str(i) + "\t" + frame.strIp + " " + str(frame))
    
    debugger.SetAsync(bAsync)

def _str_to_dict(str, delim = ':'):
    dictOut = { }
    for line in string.split(str, "\n"):
            keyVal = [s.strip() for s in string.split(line, delim, 1)]
            if len(keyVal) > 1:
                dictOut[keyVal[0]] = keyVal[1]
    return dictOut

class AnalysisEngine(object):
    def __init__(self, dictArgs):
        self.analyzers = []
        self.dictArgs = dictArgs

    def analyze(self, dictProps):
        for a in self.analyzers:
            a.analyze(dictProps, self.dictArgs)

    def add_analyzer(self, analyzer):
        self.analyzers.append(analyzer)

class SosInterpreter(object):
    def ip2md(self, strIp):
        ip2mdReturn = lldb.SBCommandReturnObject()
        strOut = self.run_command("ip2md " + strIp)
        return strOut
    
    def dumpclass(self, strClassPtr):
        strOut = self.run_command("sos DumpClass " + strClassPtr)
        return strOut

    def run_command(self, strCmd):
        strOut = ""
        result = lldb.SBCommandReturnObject()
        g_dbg.interpreter.HandleCommand(strCmd, result)
        if result.Succeeded() and result.HasResult():
            #print "INFO: Command SUCCEEDED: '" + strCmd + "'"
            strOut = result.GetOutput()
            #print strOut
        else:
            print "ERROR: Command FAILED: '" + strCmd + "'"
            print result.GetError()
        return strOut
    
class DbgFrame(object):

    def __init__(self, sbFrame):
        self.sbFrame = sbFrame
        self.strIp = self.sbFrame.addr.GetLoadAddress(g_dbg.target)
        self.strModule = sbFrame.module.file.basename
        self.strRoutine = sbFrame.symbol.name
        
        if (self.strModule is None or self.strModule == '') and (self.strRoutine is None or self.strRoutine == ''):
            self.tryget_managed_frame_info()

            
        if self.strModule is None or self.strModule == '':
            self.strModule = 'UNKNOWN'
            
        if self.strRoutine is None or self.strRoutine == '':
            self.strRoutine = 'UNKNOWN'

        self.strRoutine = string.split(self.strRoutine, '(')[0]
        self.strFrame = self.strModule + '!' + self.strRoutine

    def __str__(self):
        return self.strFrame

    def tryget_managed_frame_info(self):
        sos = SosInterpreter()
        ip2mdOut = sos.ip2md(hex(self.strIp))
        ip2mdProps = _str_to_dict(ip2mdOut)
        if 'Method Name' in ip2mdProps: 
            self.strRoutine = ip2mdProps['Method Name']
            if 'Class' in ip2mdProps:
                classPtr = ip2mdProps['Class']
                if classPtr is not None and classPtr <> '':
                    classOut = sos.dumpclass(classPtr)
                    classProps = _str_to_dict(classOut)
                    if 'File' in  classProps:
                        strFile = classProps['File']
                        self.strModule = string.rsplit(string.rsplit(strFile, '.', 1)[0], '/', 1)[1] 

class StackTriageRule(object):
    """description of class"""
    def __init__(self):
        self.strFollowup = None
        self.strFrame = None
        self.strModule = None
        self.strRoutine = None
        self.bExactModule = False
        self.bExactRoutine = False
        self.bExactFrame = False
        
    def __init__(self, strTriage):
        self.load_from_triage_string(strTriage)

    #Assumes the triage string is in the valid format <strFrame>=
    def load_from_triage_string(self, strTriage):
        splitOnEq = string.split(strTriage, "=")
        self.strFrame = splitOnEq[0]
        self.strFollowup = splitOnEq[1]
        splitOnBang = string.split(splitOnEq[0], "!")
        self.strModule = "*"
        self.strRoutine = "*"
        if(len(splitOnBang) > 1):
            self.strModule = splitOnBang[0]
            self.strRoutine = splitOnBang[1]
        elif self.strFrame.endswith("*"):
            self.strModule = self.strFrame.rstrip("*")
        elif self.strFrame.startswith("*"):
            self.strRoutine = self.strFrame.lstrip("*")
        else:
            self.strModule = self.strFrame

        self.bExactModule = "*" not in self.strModule
        self.bExactRoutine = "*" not in self.strRoutine
        self.bExactFrame = self.bExactModule and self.bExactRoutine

class StackTriageEngine(object):
    def __init__(self):
        self.dictExactFrame = { }
        self.dictExactModule = { }
        self.dictExactRoutine = { }
        self.lstWildRules = [ ]

    ## loads the specified rules into the triage engine
    ## lstRules - a list of rules to be added to the current triage engine
    def load_rules(self, lstRules):
        for r in lstRules:
            if(r.bExactFrame):
                self.dictExactFrame[r.strFrame] = r
            elif (r.bExactModule):
                self.add_to_multidict(self.dictExactModule, r.strModule, r)
            elif (r.bExactRoutine):
                self.add_to_multidict(self.dictExactRoutine, r.strRoutine, r)
            else:
                self.lstWildRules.append(r);
        self.sort_rules()

    ## finds the blame symbol for the specified stack
    ## lstFrame - list of frames in the stack to triage
    ## return - tuple (frame, rule) for the blamed symbol of the stack, 
    ##          or None if a blame symbol could not be determined for the stack
    def triage_stack(self, lstFrame):
        for frame in lstFrame:
            rule = self.find_matching_rule(frame)
            if rule is None or string.strip(rule.strFollowup.lower()) <> "ignore":
                return (frame, rule)
        return None
    
    ## finds the first rule matching the specified frame.  If no rules match None is returned
    ## frame - the frame to find matching rules for
    def find_matching_rule(self, frame):
        #initialze rule to none to return if no matching rules are found
        rule = None
        #check if frame matches exact rule
        if(frame.strFrame in self.dictExactFrame):
            rule = self.dictExactFrame[frame.strFrame];
        #check if frame matches rule with an exact module
        if (rule is None and frame.strModule in self.dictExactModule):
            ruleIdx = self.find_indexof_first_match(frame.strRoutine, [rule.strRoutine for rule in self.dictExactModule[frame.strModule]])
            if (ruleIdx >= 0):
                rule = self.dictExactModule[frame.strModule][ruleIdx]
        #check if frame matches rule with an exact routine
        if (rule is None and frame.strRoutine in self.dictExactRoutine):
            ruleIdx = self.find_indexof_first_match(frame.strRoutine, [rule.strModule for rule in self.dictExactRoutine[frame.strRoutine]])
            if (ruleIdx >= 0):
                rule = self.dictExactModule[frame.strModule][ruleIdx]
        #check if frame matches wildcard rule
        ruleIdx = self.find_indexof_first_match(frame.strRoutine, [rule.strFrame for rule in self.lstWildRules])
        if (ruleIdx >= 0):
                rule = self.lstWildRules[ruleIdx]
        return rule

    ## private - finds the index of the first wildcard expression matching the specified string
    ##           str - string to find a matching expression
    ##           lstExpr - a list of expression to evaluate against the specified string
    def find_indexof_first_match(self, str, lstExpr):
        for i, expr in enumerate(lstExpr):
            if self.is_wildcard_match(str, expr):
                return i;
        return -1;
    
    ## private - determins if the specified string matches the speicified wild card expression
    ##           str - string to evaluage against the wild card expression
    ##           expr - wildcard expression using * to match any
    ##           returns - true if the specified string matches the given expression otherwise false
    def is_wildcard_match(self, str, expr):
        match = False
        
        splitOnWild = string.split(expr, "*")
        
        findStartIdx = 0

        #if the expr doesn't start with * verify the start of the string
        if splitOnWild[0] <> "":
            if str.startswith(splitOnWild[0]):
                searchStartIdx = len(splitOnWild[0]);
            else:
                return False

        #match all the interior search strings
        for i in range(1, len(splitOnWild) - 1):
            #ignore "" as this corresponds to a ** in the triage string so we move to the next token
            if splitOnWild[i] <> "":
                matchIdx = string.find(str, splitOnWild[i], findStartIdx)
                #if the token was not found return false
                if  matchIdx == -1:
                        return False
                findStartIdx = matchIdx + len(splitOnWild[i])

        #if the expr doesn't end with * verify the end of the string
        if splitOnWild[len(splitOnWild) - 1] <> "":
            if not str.endswith(splitOnWild[len(splitOnWild) - 1]):
                return False

        #if we haven't returned yet all the search strings were found
        return True


    ## private - sorts all engine rules based of the order they should be evaluated.  In this case by their length ignoring wildcard symbols
    def sort_rules(self):
        for key in self.dictExactModule:
            self.dictExactModule[key] = sorted(self.dictExactModule[key], key=lambda rule: len(rule.strRoutine.strip("*")))
        
        for key in self.dictExactRoutine:
            self.dictExactRoutine[key] = sorted(self.dictExactRoutine[key], key=lambda rule: len(rule.strModule.strip("*")))
        
        self.lstWildRules = sorted(self.lstWildRules, key=lambda rule: len(rule.strModule.strip("*")))

    ## private - adds item to the specified multi-dictionary.  if the key doesn't exist creates a list value for the item
    def add_to_multidict(self, dict, key, val):
        if key in dict:
            dict[key].append(val)
        else:
            dict[key] = [ val ]

class StackTriageAnalyzer(AnalysisEngine):
    def __init__(self):
        self.stackTriageEng = StackTriageEngine()
        self.bLoaded = False


    def analyze(self, dictProps, dictArgs):
        #print " ".join((self.debugger, self.interpreter, self.target))

        if not self.bLoaded:
            self.load_triage_engine(dictArgs)

        #get the eventing thread stack
        lstFrame = g_dbg.get_current_stack()


        dictProps["FAULT_STACK"] = "\n".join([str(f) for f in lstFrame])

        #triage with the triage engine
        tplFrameRule = self.stackTriageEng.triage_stack(lstFrame)
        
        #if a tuple was returned 
        if tplFrameRule is not None:
            dictProps["FAULT_SYMBOL"] = tplFrameRule[0].strFrame
            # if the rule in the tuple is not null has a strFollowup
            if tplFrameRule[1] is not None and tplFrameRule[1].strFollowup is not None:
                dictProps["FOLLOW_UP"] = tplFrameRule[1].strFollowup
        else:
            dictProps["FAULT_SYMBOL"] = "UNKNOWN!UNKNOWN"


    def load_triage_engine(self, dictArgs):
        rules = []
        
        triageIni = 'triage.ini'

        if '-i' in dictArgs:
            triageIni = dictArgs['-i']

        self.load_rules_from_file(triageIni, rules)

        self.stackTriageEng.load_rules(rules)

    def load_rules_from_file(self, strPath, lstRules):
        with open(strPath) as f:
            #read all lines from the file
            rawlines = [line.rstrip('\n') for line in f]
        
        #filter all comment lines, blank lines and lines not containing a =
        ruleLines = [line for line in rawlines if len(line) <> 0 and line[0] <> '\n' and line[0] <> ';' and '=' in line]

        #create rule for each rule line
        lstRules.extend([StackTriageRule(line) for line in ruleLines])
        
