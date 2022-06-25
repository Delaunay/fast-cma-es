# Copyright (c) Dietmar Wolz.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory.

# See https://github.com/dietmarwo/fast-cma-es/blob/master/tutorials/Employee.adoc for a detailed description.

import json
import numpy as np
from dateutil.parser import parse
from numba import njit
import numba
from fcmaes.optimizer import Bite_cpp, De_cpp, Crfmnes_cpp, wrapper
from fcmaes import retry, moretry, modecpp, mode
from scipy.optimize import Bounds    

def shift_indices(shifts):
    shift_to_index = {}
    days = []
    locations = []
    skills = []
    sec_start = []
    sec_end = []
    for i in range(len(shifts)):
        shift_to_index[str(shifts[i])] = i
        day = shifts[i]['start'].split('T')[0]
        dt_start = parse(shifts[i]['start'])
        dt_end = parse(shifts[i]['end'])
        sec_start.append(dt_start.timestamp())
        sec_end.append(dt_end.timestamp())
        location = shifts[i]['location']
        required_skill = shifts[i]['required_skill']
        days.append(day)
        locations.append(location)
        skills.append(required_skill)
    return shift_to_index, days, locations, skills, \
                np.array(sec_start), np.array(sec_end)

def employee_indices(employees):
    empl_to_index = {}
    names = []
    skill_sets = []
    for i in range(len(employees)):
        empl_to_index[str(employees[i])] = i
        name = employees[i]['name']
        skill_set = employees[i]['skill_set']
        names.append(name)
        skill_sets.append(skill_set)
    return empl_to_index, names, skill_sets

def avail_indices(avails):
    avails_to_index = {}
    names = []
    types = []
    days = []
    for i in range(len(avails)):
        avails_to_index[str(avails[i])] = i
        name = avails[i]['employee']['name']
        type = avails[i]['availability_type']
        day = avails[i]['date']
        names.append(name)
        types.append(type)        
        days.append(day)
    return avails_to_index, names, types, days

def index_map(strs):
    to_index = {}
    ids = []
    j = 0
    for s in strs:
        if not s in to_index:
            to_index[s] = j
            j += 1
        ids.append(to_index[s])
    return to_index, np.array(ids)

def index_multi_map(lists):
    to_index = {}
    max_len = max([len(l) for l in lists])   
    ids = np.full((len(lists), max_len), -1, dtype=int)
    k = 0
    for i in range(len(lists)):
        l = lists[i]
        for j in range(len(l)):
            s = l[j]
            if not s in to_index:
                to_index[s] = k
                k += 1
            ids[i,j] = to_index[s]
    return to_index, np.array(ids)

DESIRED = 0
UNDESIRED = 1
UNAVAILABLE = 2
avail_type_map = {'DESIRED':DESIRED, 'UNDESIRED':UNDESIRED, 'UNAVAILABLE':UNAVAILABLE}

@njit(fastmath=True)
def fitness_(employees_at_shift, day_ids, required_skill_ids, skill_set_ids, 
             avail_names_ids, avail_days_ids, avail_type_ids, sec_start, sec_end):
    score = 0
    num_employees = len(skill_set_ids)
    employee_last_day = np.full(num_employees, -1, dtype=numba.int32)
    employee_last_end = np.full(num_employees, -1, dtype=numba.int32)
    employee_num_shifts = np.zeros(num_employees, dtype=numba.int32)
    for shift in range(len(employees_at_shift)):
        day = day_ids[shift]
        employee = employees_at_shift[shift]
        employee_num_shifts[employee] += 1
        if employee_last_day[employee] == day:
            score += 1000  # employee should only work once a day
        employee_last_day[employee] = day
        if sec_start[shift] - employee_last_end[employee] < 10*3600:
            score += 1000  # employee should pause for 10 hours (and shifts should not overlap)
        employee_last_end[employee] = sec_end[shift]
        required_skill = required_skill_ids[shift]
        skill_set = skill_set_ids[employee]
        if not required_skill in skill_set: 
            score += 1000 # employee has wrong skill set
        avail_ids = np.where(avail_names_ids == employee)
        for avail_id in avail_ids[0]:
            avail_day = avail_days_ids[avail_id]
            if day == avail_day:
                type = avail_type_ids[avail_id]
                if type == UNDESIRED:  
                    score += 100 # employee does not want to work this day
                elif type == UNAVAILABLE:
                    score += 1000 # employee is unavailable
                elif type == DESIRED:
                    score -= 100 # employee works at desired day
    return score, employee_num_shifts
        
class problem():
    
    def __init__(self, json_file):
        with open(json_file) as json_file:
            sched = json.load(json_file)    
            
        self.shifts = sched['shift_list']
        self.shift_to_index, self.days, self.locations, self.required_skills, \
                self.sec_start, self.sec_end = shift_indices(self.shifts)
        self.day_to_index, self.day_ids = index_map(self.days)
        self.location_to_index, self.location_ids = index_map(self.locations)
                
        self.employees = sched['employee_list']
        self.employee_to_index, self.names, self.skill_sets = employee_indices(self.employees)
        self.name_to_index, self.name_ids = index_map(self.names)
        self.skill_to_index, self.skill_set_ids = index_multi_map(self.skill_sets)
        self.required_skill_ids = np.array([self.skill_to_index[s] for s in self.required_skills])

        self.avails = sched['availability_list']
        self.avail_to_index, self.avail_names, self.avail_types, self.avail_days = avail_indices(self.avails)
        self.avail_name_ids = np.array([self.name_to_index[n] for n in self.avail_names])
        self.avail_day_ids = np.array([self.day_to_index[d] for d in self.avail_days])
        self.avail_type_ids = np.array([avail_type_map[t] for t in self.avail_types])
        
        print("days", self.days)
        print("day_ids",self.day_ids)
        print("day_to_index", self.day_to_index)
        print("location_ids", self.location_ids)
        print("required_skill_ids", self.required_skills)
        print("required_skills_ids", self.required_skill_ids)        
        print("names", self.names)
        print("name_ids", self.name_ids)
        print("skill_set_ids", self.skill_set_ids)
        print("avail_name_ids", self.avail_name_ids)
        print("avail_day_ids", self.avail_day_ids)
        print("avail_types", self.avail_types)
        print("avail_type_ids", self.avail_type_ids)
        
        self.dim = len(self.shifts)
        self.bounds = Bounds([0]*self.dim, [len(self.employees)-1E-9]*self.dim)  

    def fitness(self, x):
        score, employee_num_shifts = fitness_(x.astype(int), self.day_ids, 
                    self.required_skill_ids, self.skill_set_ids, self.avail_name_ids, 
                    self.avail_day_ids, self.avail_type_ids, self.sec_start, self.sec_end)
        return score + 100*np.std(employee_num_shifts)

    def fitness_mo(self, x):
        score, employee_num_shifts = fitness_(x.astype(int), self.day_ids, 
                    self.required_skill_ids, self.skill_set_ids, self.avail_name_ids, 
                    self.avail_day_ids, self.avail_type_ids, self.sec_start, self.sec_end)
        return [score, np.std(employee_num_shifts)]
        #return [score, -min(employee_num_shifts)]
    
    def show(self, x):
        employees_at_shift = x.astype(int)
        for i in range(len(employees_at_shift)):
            empl = self.employees[employees_at_shift[i]]
            shift = self.shifts[i]
            shift['employee'] = empl
            print(i, shift) 
        num_employees = len(self.skill_set_ids)
        employee_last_day = np.full(num_employees, -1, dtype=int)
        employee_last_end = np.full(num_employees, -1, dtype=int)
        employee_num_shifts = np.zeros(num_employees, dtype=int)
        for i in range(len(employees_at_shift)):
            shift = self.shifts[i]
            day = self.day_ids[i]
            employee = employees_at_shift[i]
            name = self.names[employee]
            employee_num_shifts[employee] += 1
            if employee_last_day[employee] == day:
                print("employee", name, "works twice a day", shift)
            employee_last_day[employee] = day
            if self.sec_start[i] - employee_last_end[employee] < 10*3600:
                print("employee", name, "employee should pause for 10 hours ", shift)
            employee_last_end[employee] = self.sec_end[i]
            required_skill = self.required_skill_ids[i]
            skill_set = self.skill_set_ids[employee]
            if not required_skill in skill_set: 
                print("employee", name, "has wrong skill set", shift)
        desired = 0
        for i in range(len(employees_at_shift)):
            shift = self.shifts[i]
            day = shift['start'].split('T')[0]
            empl = self.employees[employees_at_shift[i]]
            for avail in self.avails:
                aday = avail['date']
                if aday == day:
                    aempl = avail['employee']
                    if aempl == empl:
                        print(empl, avail)
                        if avail['availability_type'] == 'DESIRED':
                            desired += 1
        print("desired shift days", desired)
        print("shifts per employee", list(employee_num_shifts))
        print("min shifts per employee", min(employee_num_shifts))
        print("mean shifts per employee", np.mean(employee_num_shifts))
        print("std shifts per employee", np.std(employee_num_shifts))
        
    def optimize(self):
        self.fitness(np.random.uniform(0, len(self.employees), self.dim).astype(int))
        res = retry.minimize_plot("schedule.bite.400k", Bite_cpp(400000),  
        #res = retry.minimize_plot("schedule.bite.4000k", Bite_cpp(4000000),  
        
        #res = retry.minimize_plot("schedule.de.400k", De_cpp(400000, popsize = 512, ints = [True]*self.dim), 
        #res = retry.minimize_plot("schedule.de.10000k", De_cpp(10000000, popsize = 10000, ints = [True]*self.dim), 
        
                    wrapper(self.fitness), self.bounds, num_retries=32, plot_limit=10000)
        print(self.fitness_mo(res.x)) 
        self.show(res.x)

    def optimize_mo(self):
        self.fitness_mo(np.random.uniform(0, len(self.employees), self.dim).astype(int))
        
        pname = "schedule_mo_600k.512"    
        
        xs, ys = modecpp.retry(mode.wrapper(self.fitness_mo, 2), 
                         2, 0, self.bounds, popsize = 512, max_evaluations = 600000, 
                     nsga_update=True, num_retries = 32, workers=32)
        
        # xs, ys = modecpp.retry(mode.wrapper(self.fitness_mo, 2), 
        #          2, 0, self.bounds, popsize = 4096, max_evaluations = 40000000, 
        #      nsga_update=True, num_retries = 32, workers=32)

        np.savez_compressed(pname, xs=xs, ys=ys)
        xs, ys = moretry.pareto(xs, ys)
        for x, y in zip(xs, ys):
            print(str(list(y)) + ' ' + str([int(xi) for xi in x]))
        
def show_example_solution():
    x = [12, 5, 13, 2, 10, 6, 3, 7, 0, 14, 2, 6, 1, 0, 15, 8, 3, 9, 10, 14, 8, 11, 9, 15, 13, 1, 4, 5, 15, 0, 3, 2, 10, 12, 13, 11, 6, 4, 0, 14, 7, 9, 8, 3, 13, 8, 14, 11, 15, 3, 12, 5, 4, 7, 10, 9, 0, 2, 5, 6, 1, 11, 3, 12, 6, 14, 11, 2, 7, 0, 10, 4, 8, 3, 12, 13, 5, 1, 6, 11, 7, 12, 8, 14, 11, 6, 4, 10, 9, 5, 1, 2, 13, 4, 8, 15, 11, 7, 0, 14, 2, 13, 4, 1, 6, 15, 10, 12, 3, 6, 5, 9, 1, 7, 4, 15, 11, 0, 12, 13, 2, 10, 5, 8, 7, 15]
    # x = [10, 1, 9, 6, 11, 4, 15, 13, 0, 7, 13, 15, 14, 1, 4, 2, 6, 11, 15, 0, 8, 5, 12, 4, 9, 14, 11, 3, 15, 9, 1, 4, 7, 5, 13, 2, 8, 3, 14, 9, 2, 5, 6, 0, 1, 6, 14, 12, 15, 7, 11, 3, 8, 1, 6, 0, 9, 5, 10, 2, 11, 13, 1, 11, 12, 14, 7, 8, 3, 0, 15, 10, 6, 15, 4, 3, 7, 5, 2, 11, 13, 12, 10, 1, 15, 6, 4, 8, 9, 5, 4, 12, 13, 8, 6, 3, 10, 7, 0, 9, 8, 14, 2, 7, 10, 12, 11, 15, 3, 4, 5, 9, 14, 12, 10, 2, 1, 0, 13, 7, 3, 12, 5, 2, 10, 8]
    # x = [10, 1, 3, 6, 11, 4, 15, 13, 0, 7, 13, 15, 14, 1, 4, 2, 6, 11, 15, 0, 8, 5, 12, 4, 9, 14, 11, 3, 15, 9, 1, 4, 7, 5, 13, 2, 8, 3, 14, 9, 2, 5, 6, 0, 1, 6, 14, 12, 15, 7, 11, 3, 8, 1, 6, 0, 9, 5, 10, 2, 11, 13, 1, 11, 12, 14, 7, 8, 3, 0, 15, 10, 6, 13, 4, 9, 7, 5, 2, 11, 14, 12, 10, 0, 15, 6, 4, 8, 9, 5, 4, 12, 13, 8, 6, 3, 10, 7, 0, 9, 8, 14, 2, 7, 10, 12, 11, 15, 3, 4, 5, 9, 14, 12, 10, 2, 1, 0, 13, 7, 3, 12, 5, 2, 10, 8]
 
    p.show(np.array(x))
    print(list(x))
           
if __name__ == '__main__':
    p = problem('data/sched1.json')
    # p = problem('data/sched2.json')
    p.optimize()
    # p.optimize_mo()
    # show_example_solution()

    
